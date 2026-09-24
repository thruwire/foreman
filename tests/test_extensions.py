from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from foreman.cli import app
from foreman.config import FactoryConfig
from foreman.extensions import (
    ExtensionContribution,
    ExtensionError,
    ExtensionIdentity,
    ExtensionManager,
    ExtensionManifest,
    ExtensionSnapshot,
    ResponsibilityRegistration,
    discover_extensions,
)
from foreman.responsibilities import (
    Check,
    CheckFileConfig,
    ResponsibilityFileConfig,
    ResponsibilityRoute,
    configured_registry,
)
from foreman.routing import RouteGroup


class MemoryCredentials:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get(self, extension_id: str, key: str) -> str | None:
        return self.values.get((extension_id, key))

    def set(self, extension_id: str, key: str, value: str) -> None:
        self.values[(extension_id, key)] = value

    def delete(self, extension_id: str, key: str) -> None:
        self.values.pop((extension_id, key), None)


@dataclass
class ExampleResponsibility:
    id: str = "example.review"
    check_definitions: tuple[Check, ...] = ()

    def configured(self, settings):
        assert settings == {}
        return self

    def configured_checks(self, checks):
        return replace(self, check_definitions=tuple(checks))

    def route(self) -> ResponsibilityRoute:
        return ResponsibilityRoute(always=True)

    def checks(self):
        return self.check_definitions

    def directives(self, state, result):
        del state, result
        return []


def registration() -> ResponsibilityRegistration:
    return ResponsibilityRegistration(
        implementation=ExampleResponsibility(),
        definition=ResponsibilityFileConfig(
            always=True,
            checks={
                "required": CheckFileConfig(
                    instructions="Is the extension review required?",
                    min_threshold=0.8,
                )
            },
        ),
    )


class StaticExtension:
    manifest = ExtensionManifest(id="example")

    def __init__(self) -> None:
        self.activations = 0
        self.closed = False

    def activate(self, context, snapshot):
        assert context.settings == {}
        assert snapshot is None
        self.activations += 1
        return ExtensionContribution(
            responsibilities=(registration(),),
            routing_root=RouteGroup(
                id="example.root",
                route=ResponsibilityRoute(always=True),
                responsibility_ids=("example.review",),
            ),
        )

    async def close(self) -> None:
        self.closed = True


class SyncingExtension(StaticExtension):
    def __init__(self) -> None:
        super().__init__()
        self.login_calls = 0
        self.sync_calls = 0
        self.logout_calls = 0

    async def login(self, context):
        self.login_calls += 1
        context.credentials.set("example", "refresh_token", "secret")
        return ExtensionIdentity(subject="user-123", display_name="Example User")

    async def sync(self, context):
        self.sync_calls += 1
        assert context.credentials.get("example", "refresh_token") == "secret"
        return ExtensionSnapshot(
            extension_id="example",
            schema_version=1,
            revision=f"revision-{self.sync_calls}",
            payload={"projects": [{"id": "project-1", "name": "Project One"}]},
        )

    async def logout(self, context):
        self.logout_calls += 1
        context.credentials.delete("example", "refresh_token")

    def activate(self, context, snapshot):
        assert snapshot is not None
        assert snapshot.payload["projects"][0]["id"] == "project-1"
        self.activations += 1
        return ExtensionContribution(
            responsibilities=(registration(),),
            routing_root=RouteGroup(
                id="example.root",
                route=ResponsibilityRoute(always=True),
                responsibility_ids=("example.review",),
                bindings={"project_id": "project-1"},
            ),
        )


def write_config(tmp_path, body: str = '[extensions."example"]\n') -> None:
    (tmp_path / "config.toml").write_text(body, encoding="utf-8")


def test_config_presence_enables_only_named_extensions(tmp_path) -> None:
    write_config(tmp_path)
    extension = StaticExtension()
    ignored = StaticExtension()
    ignored.manifest = ExtensionManifest(id="ignored")

    manager = ExtensionManager(
        data_dir=tmp_path,
        extensions={"example": extension, "ignored": ignored},
        credentials=MemoryCredentials(),
    )
    activated = manager.activate(FactoryConfig())

    assert manager.enabled_ids == ("example",)
    assert [item.implementation.id for item in activated.responsibilities] == ["example.review"]
    assert [item.id for item in activated.routing_groups] == ["example.root"]
    assert ignored.activations == 0


def test_configured_but_uninstalled_extension_fails(tmp_path) -> None:
    write_config(tmp_path)

    with pytest.raises(ExtensionError, match="not installed: example"):
        ExtensionManager(data_dir=tmp_path, extensions={})


def test_discovery_imports_only_configured_entry_points(monkeypatch) -> None:
    loaded: list[str] = []

    class EntryPoint:
        def __init__(self, name, factory):
            self.name = name
            self.factory = factory

        def load(self):
            loaded.append(self.name)
            return self.factory

    class EntryPoints(list):
        def select(self, **kwargs):
            assert kwargs == {"group": "foreman.extensions"}
            return self

    monkeypatch.setattr(
        "foreman.extensions.metadata.entry_points",
        lambda: EntryPoints(
            [
                EntryPoint("example", lambda: StaticExtension()),
                EntryPoint("ignored", lambda: (_ for _ in ()).throw(RuntimeError("boom"))),
            ]
        ),
    )

    discovered = discover_extensions({"example"})

    assert list(discovered) == ["example"]
    assert loaded == ["example"]


def test_extension_responsibilities_use_existing_configuration_pipeline(tmp_path) -> None:
    write_config(tmp_path)
    manager = ExtensionManager(
        data_dir=tmp_path,
        extensions={"example": StaticExtension()},
        credentials=MemoryCredentials(),
    )
    activated = manager.activate(FactoryConfig())

    registry = configured_registry(
        FactoryConfig(),
        additional=(item.implementation for item in activated.responsibilities),
        additional_configs={
            item.implementation.id: item.definition for item in activated.responsibilities
        },
    )

    extension = next(item for item in registry.responsibilities if item.id == "example.review")
    assert extension.checks()[0].instructions == "Is the extension review required?"
    assert registry.route_for("example.review").always is True


@pytest.mark.asyncio
async def test_login_synchronizes_once_and_activation_only_reads_cache(tmp_path) -> None:
    write_config(tmp_path)
    extension = SyncingExtension()
    credentials = MemoryCredentials()
    manager = ExtensionManager(
        data_dir=tmp_path,
        extensions={"example": extension},
        credentials=credentials,
    )

    identity, snapshot = await manager.login("example")
    first = manager.activate(FactoryConfig())
    second = manager.activate(FactoryConfig())

    assert identity.display_name == "Example User"
    assert snapshot is not None and snapshot.revision == "revision-1"
    assert extension.login_calls == 1
    assert extension.sync_calls == 1
    assert extension.activations == 2
    assert first.snapshot_revisions == {"example": "revision-1"}
    assert second.snapshot_revisions == {"example": "revision-1"}
    assert manager.snapshots.path_for("example").exists()


def test_syncable_extension_without_snapshot_fails_closed(tmp_path) -> None:
    write_config(tmp_path)
    manager = ExtensionManager(
        data_dir=tmp_path,
        extensions={"example": SyncingExtension()},
        credentials=MemoryCredentials(),
    )

    with pytest.raises(ExtensionError, match="requires synchronization"):
        manager.activate(FactoryConfig())


def test_expired_snapshot_fails_closed_without_syncing(tmp_path) -> None:
    write_config(tmp_path)
    extension = SyncingExtension()
    manager = ExtensionManager(
        data_dir=tmp_path,
        extensions={"example": extension},
        credentials=MemoryCredentials(),
    )
    manager.snapshots.save(
        ExtensionSnapshot(
            extension_id="example",
            schema_version=1,
            revision="expired",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )

    with pytest.raises(ExtensionError, match="expired"):
        manager.activate(FactoryConfig())
    assert extension.sync_calls == 0


@pytest.mark.asyncio
async def test_logout_removes_credentials_and_cached_snapshot(tmp_path) -> None:
    write_config(tmp_path)
    extension = SyncingExtension()
    credentials = MemoryCredentials()
    manager = ExtensionManager(
        data_dir=tmp_path,
        extensions={"example": extension},
        credentials=credentials,
    )
    await manager.login("example")

    await manager.logout("example")

    assert extension.logout_calls == 1
    assert credentials.get("example", "refresh_token") is None
    assert manager.snapshots.load("example") is None


@pytest.mark.asyncio
async def test_explicit_sync_recovers_from_malformed_cached_snapshot(tmp_path) -> None:
    write_config(tmp_path)
    extension = SyncingExtension()
    credentials = MemoryCredentials()
    credentials.set("example", "refresh_token", "secret")
    manager = ExtensionManager(
        data_dir=tmp_path,
        extensions={"example": extension},
        credentials=credentials,
    )
    path = manager.snapshots.path_for("example")
    path.parent.mkdir(parents=True)
    path.write_text("{malformed", encoding="utf-8")

    snapshot = await manager.sync("example")

    assert snapshot.revision == "revision-1"
    assert manager.snapshots.load("example") == snapshot


@pytest.mark.asyncio
async def test_failed_sync_preserves_previous_valid_snapshot(tmp_path) -> None:
    write_config(tmp_path)

    class FailingSyncExtension(SyncingExtension):
        async def sync(self, context):
            del context
            raise RuntimeError("service unavailable")

    manager = ExtensionManager(
        data_dir=tmp_path,
        extensions={"example": FailingSyncExtension()},
        credentials=MemoryCredentials(),
    )
    previous = ExtensionSnapshot(
        extension_id="example",
        schema_version=1,
        revision="previous",
    )
    manager.snapshots.save(previous)

    with pytest.raises(ExtensionError, match="service unavailable"):
        await manager.sync("example")

    assert manager.snapshots.load("example") == previous


def test_extension_cannot_route_builtin_responsibilities(tmp_path) -> None:
    write_config(tmp_path)

    class InvalidExtension(StaticExtension):
        def activate(self, context, snapshot):
            del context, snapshot
            return ExtensionContribution(
                responsibilities=(registration(),),
                routing_root=RouteGroup(
                    id="example.root",
                    route=ResponsibilityRoute(always=True),
                    responsibility_ids=("core.completion",),
                ),
            )

    manager = ExtensionManager(
        data_dir=tmp_path,
        extensions={"example": InvalidExtension()},
        credentials=MemoryCredentials(),
    )

    with pytest.raises(ExtensionError, match="outside its contribution"):
        manager.activate(FactoryConfig())


def test_extension_login_cli_authenticates_and_synchronizes(
    tmp_path, monkeypatch
) -> None:
    write_config(tmp_path)
    extension = SyncingExtension()
    credentials = MemoryCredentials()
    monkeypatch.setattr(
        "foreman.extensions.discover_extensions",
        lambda configured_ids=None: {"example": extension},
    )
    monkeypatch.setattr(
        "foreman.extensions.KeyringCredentialStore",
        lambda: credentials,
    )

    result = CliRunner().invoke(
        app,
        ["extension", "login", "example", "--data-dir", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert "Authenticated example as Example User" in result.output
    assert "Synchronized revision revision-1" in result.output
    assert extension.login_calls == 1
    assert extension.sync_calls == 1

    payload = {
        "session_id": "session-1",
        "cwd": str(tmp_path),
        "hook_event_name": "SessionStart",
    }
    runner = CliRunner()
    first = runner.invoke(
        app,
        ["hook", "--data-dir", str(tmp_path)],
        input=json.dumps(payload),
    )
    second = runner.invoke(
        app,
        ["hook", "--data-dir", str(tmp_path)],
        input=json.dumps(payload),
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert extension.activations == 2
    assert extension.sync_calls == 1

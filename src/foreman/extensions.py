from __future__ import annotations

import os
import re
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
    model_validator,
)

from foreman.config import FactoryConfig
from foreman.paths import foreman_config_path, foreman_data_dir
from foreman.responsibilities import Responsibility, ResponsibilityFileConfig
from foreman.routing import RouteGroup

EXTENSION_ENTRY_POINT_GROUP = "foreman.extensions"
_EXTENSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class ExtensionError(RuntimeError):
    """An installed or configured Foreman extension could not be used safely."""


class ForemanFileConfig(BaseModel):
    """Repository-independent Foreman configuration."""

    model_config = ConfigDict(extra="forbid")

    # Presence in this mapping enables an extension. Values belong to that extension.
    extensions: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)

    @field_validator("extensions")
    @classmethod
    def valid_extension_ids(
        cls, value: dict[str, dict[str, JsonValue]]
    ) -> dict[str, dict[str, JsonValue]]:
        invalid = sorted(item for item in value if _EXTENSION_ID.fullmatch(item) is None)
        if invalid:
            raise ValueError(f"invalid extension ids: {', '.join(invalid)}")
        return value


class ExtensionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    api_version: int = Field(default=1, ge=1)
    snapshot_schema_version: int = Field(default=1, ge=1)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if _EXTENSION_ID.fullmatch(value) is None:
            raise ValueError(f"invalid extension id: {value!r}")
        return value


class ExtensionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extension_id: str
    schema_version: int = Field(ge=1)
    revision: str = Field(min_length=1, max_length=500)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("extension_id")
    @classmethod
    def valid_extension_id(cls, value: str) -> str:
        if _EXTENSION_ID.fullmatch(value) is None:
            raise ValueError(f"invalid extension id: {value!r}")
        return value

    @model_validator(mode="after")
    def aware_timestamps(self) -> ExtensionSnapshot:
        if self.fetched_at.tzinfo is None:
            raise ValueError("snapshot fetched_at must include a timezone")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("snapshot expires_at must include a timezone")
        return self


class ExtensionIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1)
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class ResponsibilityRegistration:
    implementation: Responsibility
    definition: ResponsibilityFileConfig


@dataclass(frozen=True, slots=True)
class ExtensionContribution:
    responsibilities: tuple[ResponsibilityRegistration, ...] = ()
    routing_root: RouteGroup | None = None


@dataclass(frozen=True, slots=True)
class ExtensionActivationContext:
    factory_config: FactoryConfig
    data_dir: Path
    settings: Mapping[str, JsonValue]


class CredentialStore(Protocol):
    def get(self, extension_id: str, key: str) -> str | None: ...

    def set(self, extension_id: str, key: str, value: str) -> None: ...

    def delete(self, extension_id: str, key: str) -> None: ...


class KeyringCredentialStore:
    """Store extension secrets in the operating system's configured keyring."""

    service_prefix = "foreman.extension"

    @staticmethod
    def _keyring() -> Any:
        try:
            import keyring
        except ImportError as error:
            raise ExtensionError(
                "extension authentication requires the 'keyring' package"
            ) from error
        return keyring

    def _service(self, extension_id: str) -> str:
        return f"{self.service_prefix}.{extension_id}"

    def get(self, extension_id: str, key: str) -> str | None:
        return self._keyring().get_password(self._service(extension_id), key)

    def set(self, extension_id: str, key: str, value: str) -> None:
        self._keyring().set_password(self._service(extension_id), key, value)

    def delete(self, extension_id: str, key: str) -> None:
        keyring = self._keyring()
        try:
            keyring.delete_password(self._service(extension_id), key)
        except keyring.errors.PasswordDeleteError:
            pass


@dataclass(frozen=True, slots=True)
class ExtensionLoginContext:
    data_dir: Path
    settings: Mapping[str, JsonValue]
    credentials: CredentialStore


@dataclass(frozen=True, slots=True)
class ExtensionSyncContext:
    data_dir: Path
    settings: Mapping[str, JsonValue]
    credentials: CredentialStore
    previous_snapshot: ExtensionSnapshot | None


class ForemanExtension(Protocol):
    manifest: ExtensionManifest

    def activate(
        self,
        context: ExtensionActivationContext,
        snapshot: ExtensionSnapshot | None,
    ) -> ExtensionContribution: ...

    async def close(self) -> None: ...


class ExtensionSnapshotStore:
    """Atomic owner-readable storage for synchronized extension snapshots."""

    max_snapshot_bytes = 10_000_000

    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = foreman_data_dir(data_dir)
        self.extensions_dir = self.data_dir / "extensions"

    def path_for(self, extension_id: str) -> Path:
        if _EXTENSION_ID.fullmatch(extension_id) is None:
            raise ExtensionError(f"invalid extension id: {extension_id!r}")
        return self.extensions_dir / extension_id / "snapshot.json"

    def load(self, extension_id: str) -> ExtensionSnapshot | None:
        path = self.path_for(extension_id)
        try:
            if path.stat().st_size > self.max_snapshot_bytes:
                raise ExtensionError(
                    f"snapshot for extension {extension_id!r} exceeds "
                    f"{self.max_snapshot_bytes} bytes"
                )
            return ExtensionSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValidationError, ValueError) as error:
            raise ExtensionError(
                f"snapshot for extension {extension_id!r} is malformed: {error}"
            ) from error

    def save(self, snapshot: ExtensionSnapshot) -> None:
        target = self.path_for(snapshot.extension_id)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(target.parent, 0o700)
        except OSError:
            pass
        payload = snapshot.model_dump_json(indent=2)
        if len(payload.encode("utf-8")) > self.max_snapshot_bytes:
            raise ExtensionError(
                f"snapshot for extension {snapshot.extension_id!r} exceeds "
                f"{self.max_snapshot_bytes} bytes"
            )
        descriptor, temporary = tempfile.mkstemp(
            prefix="snapshot-", suffix=".tmp", dir=target.parent
        )
        try:
            try:
                os.fchmod(descriptor, 0o600)
            except OSError:
                pass
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def delete(self, extension_id: str) -> None:
        try:
            self.path_for(extension_id).unlink()
        except FileNotFoundError:
            pass


@dataclass(frozen=True, slots=True)
class ActivatedExtensions:
    extension_ids: tuple[str, ...]
    responsibilities: tuple[ResponsibilityRegistration, ...]
    routing_groups: tuple[RouteGroup, ...]
    snapshot_revisions: Mapping[str, str]


def load_foreman_file_config(
    *,
    data_dir: Path | str | None = None,
    config_path: Path | str | None = None,
) -> ForemanFileConfig:
    path = foreman_config_path(data_dir, config_path)
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
        return ForemanFileConfig.model_validate(payload)
    except FileNotFoundError:
        return ForemanFileConfig()
    except (OSError, tomllib.TOMLDecodeError, ValidationError, ValueError) as error:
        raise ExtensionError(f"invalid Foreman config {path}: {error}") from error


def discover_extensions(
    configured_ids: set[str] | None = None,
) -> dict[str, ForemanExtension]:
    discovered: dict[str, ForemanExtension] = {}
    entry_points = metadata.entry_points()
    selected = (
        entry_points.select(group=EXTENSION_ENTRY_POINT_GROUP)
        if hasattr(entry_points, "select")
        else entry_points.get(EXTENSION_ENTRY_POINT_GROUP, ())
    )
    for entry_point in selected:
        if configured_ids is not None and entry_point.name not in configured_ids:
            continue
        try:
            loaded = entry_point.load()
            extension = (
                loaded()
                if isinstance(loaded, type)
                or (callable(loaded) and not hasattr(loaded, "manifest"))
                else loaded
            )
            manifest = ExtensionManifest.model_validate(extension.manifest)
        except Exception as error:
            raise ExtensionError(
                f"could not load extension entry point {entry_point.name!r}: {error}"
            ) from error
        if manifest.id != entry_point.name:
            raise ExtensionError(
                f"extension entry point {entry_point.name!r} registered as {manifest.id!r}"
            )
        if manifest.id in discovered:
            raise ExtensionError(f"duplicate installed extension id: {manifest.id}")
        discovered[manifest.id] = extension
    return discovered


class ExtensionManager:
    def __init__(
        self,
        *,
        data_dir: Path | str | None = None,
        config_path: Path | str | None = None,
        extensions: Mapping[str, ForemanExtension] | None = None,
        credentials: CredentialStore | None = None,
    ) -> None:
        self.data_dir = foreman_data_dir(data_dir)
        self.config = load_foreman_file_config(
            data_dir=self.data_dir,
            config_path=config_path,
        )
        self._installed = (
            dict(extensions)
            if extensions is not None
            else discover_extensions(set(self.config.extensions))
        )
        self.credentials = credentials or KeyringCredentialStore()
        self.snapshots = ExtensionSnapshotStore(self.data_dir)
        self._enabled = self._resolve_enabled()

    @staticmethod
    def _manifest(extension_id: str, extension: ForemanExtension) -> ExtensionManifest:
        try:
            return ExtensionManifest.model_validate(extension.manifest)
        except (AttributeError, ValidationError, ValueError) as error:
            raise ExtensionError(
                f"extension {extension_id!r} has an invalid manifest: {error}"
            ) from error

    def _resolve_enabled(self) -> dict[str, ForemanExtension]:
        missing = set(self.config.extensions) - set(self._installed)
        if missing:
            raise ExtensionError(
                "configured extensions are not installed: " + ", ".join(sorted(missing))
            )
        enabled: dict[str, ForemanExtension] = {}
        for extension_id in self.config.extensions:
            extension = self._installed[extension_id]
            manifest = self._manifest(extension_id, extension)
            if manifest.id != extension_id:
                raise ExtensionError(
                    f"configured extension {extension_id!r} registered as {manifest.id!r}"
                )
            if manifest.api_version != 1:
                raise ExtensionError(
                    f"extension {extension_id!r} requires unsupported API version "
                    f"{manifest.api_version}"
                )
            enabled[extension_id] = extension
        return enabled

    @property
    def enabled_ids(self) -> tuple[str, ...]:
        return tuple(self._enabled)

    def _validated_snapshot(
        self,
        extension_id: str,
        extension: ForemanExtension,
        *,
        required: bool,
    ) -> ExtensionSnapshot | None:
        manifest = self._manifest(extension_id, extension)
        snapshot = self.snapshots.load(manifest.id)
        sync = getattr(extension, "sync", None)
        if snapshot is None:
            if required or callable(sync):
                raise ExtensionError(
                    f"extension {manifest.id!r} requires synchronization; run: "
                    f"foreman extension sync {manifest.id}"
                )
            return None
        if snapshot.extension_id != manifest.id:
            raise ExtensionError(f"snapshot identity mismatch for extension {manifest.id!r}")
        if snapshot.schema_version != manifest.snapshot_schema_version:
            raise ExtensionError(
                f"snapshot for extension {manifest.id!r} uses schema "
                f"{snapshot.schema_version}, expected {manifest.snapshot_schema_version}"
            )
        if snapshot.expires_at is not None and snapshot.expires_at <= datetime.now(UTC):
            raise ExtensionError(
                f"snapshot for extension {manifest.id!r} expired; run: "
                f"foreman extension sync {manifest.id}"
            )
        return snapshot

    def activate(self, factory_config: FactoryConfig) -> ActivatedExtensions:
        registrations: list[ResponsibilityRegistration] = []
        routing_groups: list[RouteGroup] = []
        revisions: dict[str, str] = {}
        responsibility_ids: set[str] = set()
        group_ids: set[str] = set()
        for extension_id, extension in self._enabled.items():
            snapshot = self._validated_snapshot(extension_id, extension, required=False)
            try:
                contribution = extension.activate(
                    ExtensionActivationContext(
                        factory_config=factory_config,
                        data_dir=self.data_dir,
                        settings=self.config.extensions[extension_id],
                    ),
                    snapshot,
                )
            except ExtensionError:
                raise
            except Exception as error:
                raise ExtensionError(
                    f"extension {extension_id!r} activation failed: "
                    f"{type(error).__name__}: {error}"
                ) from error
            if not isinstance(contribution, ExtensionContribution):
                raise ExtensionError(
                    f"extension {extension_id!r} returned an invalid contribution"
                )
            if snapshot is not None:
                revisions[extension_id] = snapshot.revision
            extension_responsibility_ids: set[str] = set()
            for registration in contribution.responsibilities:
                responsibility_id = registration.implementation.id
                if not responsibility_id.startswith(f"{extension_id}."):
                    raise ExtensionError(
                        f"extension {extension_id!r} contributed responsibility "
                        f"outside its namespace: {responsibility_id!r}"
                    )
                if responsibility_id in responsibility_ids:
                    raise ExtensionError(f"duplicate extension responsibility: {responsibility_id}")
                responsibility_ids.add(responsibility_id)
                extension_responsibility_ids.add(responsibility_id)
                registrations.append(registration)
            if contribution.routing_root is not None:
                def validate_group(
                    group: RouteGroup,
                    *,
                    namespace: str = extension_id,
                    owned_ids: frozenset[str] = frozenset(extension_responsibility_ids),
                ) -> None:
                    if not group.id.startswith(f"{namespace}."):
                        raise ExtensionError(
                            f"extension {namespace!r} contributed routing outside its "
                            f"namespace: {group.id!r}"
                        )
                    if group.id in group_ids:
                        raise ExtensionError(f"duplicate extension routing group: {group.id}")
                    group_ids.add(group.id)
                    unknown = set(group.responsibility_ids) - owned_ids
                    if unknown:
                        raise ExtensionError(
                            f"extension routing group {group.id!r} references responsibilities "
                            f"outside its contribution: {', '.join(sorted(unknown))}"
                        )
                    for child in group.children:
                        validate_group(child)

                validate_group(contribution.routing_root)
                routing_groups.append(contribution.routing_root)
        return ActivatedExtensions(
            extension_ids=tuple(self._enabled),
            responsibilities=tuple(registrations),
            routing_groups=tuple(routing_groups),
            snapshot_revisions=revisions,
        )

    def extension(self, extension_id: str) -> ForemanExtension:
        try:
            return self._enabled[extension_id]
        except KeyError:
            raise ExtensionError(f"extension {extension_id!r} is not configured") from None

    async def sync(self, extension_id: str) -> ExtensionSnapshot:
        extension = self.extension(extension_id)
        sync = getattr(extension, "sync", None)
        if not callable(sync):
            raise ExtensionError(f"extension {extension_id!r} does not support synchronization")
        try:
            previous_snapshot = self.snapshots.load(extension_id)
        except ExtensionError:
            # Explicit synchronization is the recovery path for a malformed local snapshot.
            previous_snapshot = None
        try:
            snapshot = await sync(
                ExtensionSyncContext(
                    data_dir=self.data_dir,
                    settings=self.config.extensions[extension_id],
                    credentials=self.credentials,
                    previous_snapshot=previous_snapshot,
                )
            )
        except ExtensionError:
            raise
        except Exception as error:
            raise ExtensionError(
                f"extension {extension_id!r} synchronization failed: "
                f"{type(error).__name__}: {error}"
            ) from error
        manifest = self._manifest(extension_id, extension)
        try:
            validated = ExtensionSnapshot.model_validate(snapshot)
        except (ValidationError, ValueError) as error:
            raise ExtensionError(
                f"extension {extension_id!r} returned an invalid snapshot: {error}"
            ) from error
        if validated.extension_id != extension_id:
            raise ExtensionError(f"extension {extension_id!r} returned a snapshot for another id")
        if validated.schema_version != manifest.snapshot_schema_version:
            raise ExtensionError(
                f"extension {extension_id!r} returned unsupported snapshot schema "
                f"{validated.schema_version}"
            )
        if validated.expires_at is not None and validated.expires_at <= datetime.now(UTC):
            raise ExtensionError(f"extension {extension_id!r} returned an expired snapshot")
        self.snapshots.save(validated)
        return validated

    async def login(self, extension_id: str) -> tuple[ExtensionIdentity, ExtensionSnapshot | None]:
        extension = self.extension(extension_id)
        login = getattr(extension, "login", None)
        if not callable(login):
            raise ExtensionError(f"extension {extension_id!r} does not support authentication")
        try:
            identity = ExtensionIdentity.model_validate(
                await login(
                    ExtensionLoginContext(
                        data_dir=self.data_dir,
                        settings=self.config.extensions[extension_id],
                        credentials=self.credentials,
                    )
                )
            )
        except ExtensionError:
            raise
        except Exception as error:
            raise ExtensionError(
                f"extension {extension_id!r} login failed: "
                f"{type(error).__name__}: {error}"
            ) from error
        snapshot = (
            await self.sync(extension_id)
            if callable(getattr(extension, "sync", None))
            else None
        )
        return identity, snapshot

    async def logout(self, extension_id: str) -> None:
        extension = self.extension(extension_id)
        logout = getattr(extension, "logout", None)
        if not callable(logout):
            raise ExtensionError(f"extension {extension_id!r} does not support authentication")
        try:
            await logout(
                ExtensionLoginContext(
                    data_dir=self.data_dir,
                    settings=self.config.extensions[extension_id],
                    credentials=self.credentials,
                )
            )
        except ExtensionError:
            raise
        except Exception as error:
            raise ExtensionError(
                f"extension {extension_id!r} logout failed: "
                f"{type(error).__name__}: {error}"
            ) from error
        self.snapshots.delete(extension_id)

    def status(self) -> list[dict[str, object]]:
        statuses: list[dict[str, object]] = []
        for extension_id, extension in self._enabled.items():
            manifest = self._manifest(extension_id, extension)
            snapshot = self.snapshots.load(extension_id)
            statuses.append(
                {
                    "id": extension_id,
                    "api_version": manifest.api_version,
                    "supports_login": callable(getattr(extension, "login", None)),
                    "supports_sync": callable(getattr(extension, "sync", None)),
                    "snapshot_revision": snapshot.revision if snapshot else None,
                    "snapshot_expires_at": snapshot.expires_at if snapshot else None,
                }
            )
        return statuses

    async def close(self) -> None:
        errors: list[Exception] = []
        for extension in self._enabled.values():
            close = getattr(extension, "close", None)
            if callable(close):
                try:
                    await close()
                except Exception as error:
                    errors.append(error)
        if errors:
            raise ExtensionError(f"failed to close extension: {errors[0]}")

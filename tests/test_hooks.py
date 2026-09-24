from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from foreman.cli import app
from foreman.config import FactoryConfig
from foreman.foreman import ForemanModelError
from foreman.hook_adapters import CodexHookAdapter, hook_adapter
from foreman.hooks import (
    AttachedSessionStore,
    AttachedWorkerRuntime,
    HookAction,
    HookError,
    HookEvent,
    HookEventKind,
)
from foreman.models import FactoryStatus, ForemanResult, WorkerStatus
from foreman.responsibilities import ResponsibilityRoute, configured_registry
from foreman.routing import ResponsibilityRoutingError, RouteGroup, RoutingDecision


class StubRouter:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.closed = False

    async def route(self, work, candidates):
        self.calls.append(work)
        active = list(candidates.global_ids())
        candidate_ids = {item.id for item in candidates.responsibilities}
        scores = (
            {"quality.documentation": 0.94}
            if "quality.documentation" in candidate_ids
            else {}
        )
        if "documentation" in work.lower() and "quality.documentation" in candidate_ids:
            active.append("quality.documentation")
        return RoutingDecision(active_responsibility_ids=active, scores=scores)

    async def close(self) -> None:
        self.closed = True

    async def route_groups(self, work, groups):
        del work
        return RoutingDecision(
            active_responsibility_ids=[
                group_id for group_id, route in groups.items() if route.always
            ]
        )


class FailingRouter(StubRouter):
    async def route(self, work, candidates):
        del work, candidates
        raise ResponsibilityRoutingError("Jev router unavailable")


class StubModel:
    def __init__(self, **scores: float) -> None:
        self.scores = scores
        self.calls = []
        self.closed = False

    async def assess(self, observation, checks):
        self.calls.append((observation, checks))
        defaults = {
            "implementation_complete": 0.2,
            "requirements_satisfied": 0.2,
            "ready_to_finish": 0.1,
            "tests_sufficient": 0.2,
            "needs_verification": 0.2,
            "meaningful_progress": 0.9,
            "worker_stuck": 0.0,
            "work_off_track": 0.0,
            "agents_md_drift": 0.0,
            "needs_human": 0.0,
            "documentation_sufficient": 0.2,
        }
        defaults.update(self.scores)
        grouped = {}
        for check in checks:
            grouped.setdefault(check.responsibility_id, {})[check.check_id] = defaults[
                check.check_id
            ]
        return ForemanResult(checks=grouped)

    async def close(self) -> None:
        self.closed = True


class FailingModel(StubModel):
    async def assess(self, observation, checks):
        del observation, checks
        raise ForemanModelError("Jev unavailable")


def config(**updates) -> FactoryConfig:
    values = {
        "jev_timeout_seconds": 0.5,
        "steering_grace_seconds": 0,
        "hook_session_ttl_seconds": 60,
    }
    values.update(updates)
    return FactoryConfig(**values)


def event(name: str, repository, **updates):
    payload = {
        "session_id": "thr-attached-123",
        "cwd": str(repository),
        "hook_event_name": name,
        "turn_id": "turn-1",
    }
    payload.update(updates)
    return payload


async def handle(supervisor: AttachedWorkerRuntime, payload: dict) -> dict:
    adapter = CodexHookAdapter()
    event = adapter.parse(payload)
    outcome = await supervisor.handle(event)
    return adapter.render(event, outcome)


def runtime(tmp_path, model=None, runtime_config=None):
    runtime_config = runtime_config or config()
    store = AttachedSessionStore(
        tmp_path / "foreman-data",
        ttl_seconds=runtime_config.hook_session_ttl_seconds,
    )
    router = StubRouter()
    supervisor = AttachedWorkerRuntime(
        model=model or StubModel(),
        router=router,
        responsibilities=configured_registry(runtime_config),
        config=runtime_config,
        store=store,
    )
    return supervisor, store, router


@pytest.mark.asyncio
async def test_prompt_routes_multiple_built_in_toml_responsibilities(tmp_path) -> None:
    supervisor, store, router = runtime(tmp_path)

    assert await handle(supervisor, event("SessionStart", tmp_path, source="startup")) == {}
    output = await handle(
        supervisor,
        event(
            "UserPromptSubmit",
            tmp_path,
            prompt="Implement the feature and update its documentation",
        )
    )

    session = store.load("thr-attached-123")
    assert session is not None and session.state is not None
    assert router.calls == ["Implement the feature and update its documentation"]
    assert "quality.documentation" in session.state.active_responsibility_ids
    assert "core.completion" in session.state.active_responsibility_ids
    assert "repository.instructions" in session.state.active_responsibility_ids
    assert "quality.documentation" in output["hookSpecificOutput"]["additionalContext"]
    assert not (tmp_path / ".foreman").exists()


@pytest.mark.asyncio
async def test_post_tool_use_steers_attached_worker_and_persists_result(tmp_path) -> None:
    model = StubModel(work_off_track=0.96)
    supervisor, store, _ = runtime(tmp_path, model)
    await handle(supervisor, event("UserPromptSubmit", tmp_path, prompt="Implement the feature"))

    output = await handle(
        supervisor,
        event(
            "PostToolUse",
            tmp_path,
            tool_name="Bash",
            tool_input={"command": "git status --short"},
            tool_response={"output": " M src/app.py", "exit_code": 0},
        )
    )

    assert output == {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "active worker appears off track",
        }
    }
    session = store.load("thr-attached-123")
    assert session is not None and session.state is not None
    assert session.state.latest_intervention is not None
    assert session.state.latest_intervention.responsibility_id == "core.worker-health"
    assert session.state.workers[0].steer_count == 1
    assert "git status --short" in model.calls[0][0].latest_worker_output


@pytest.mark.asyncio
async def test_attached_checks_do_not_use_managed_run_iteration_ceiling(tmp_path) -> None:
    model = StubModel(work_off_track=0.96)
    supervisor, _, _ = runtime(tmp_path, model, config(max_iterations=1))
    await handle(supervisor, event("UserPromptSubmit", tmp_path, prompt="Implement it"))

    output = await handle(
        supervisor,
        event(
            "PostToolUse",
            tmp_path,
            tool_name="Bash",
            tool_input={"command": "git status --short"},
            tool_response={"output": "", "exit_code": 0},
        )
    )

    assert output["hookSpecificOutput"]["additionalContext"] == ("active worker appears off track")


@pytest.mark.asyncio
async def test_stop_continues_unfinished_work_once(tmp_path) -> None:
    supervisor, store, _ = runtime(tmp_path)
    await handle(supervisor, event("UserPromptSubmit", tmp_path, prompt="Implement it"))

    output = await handle(
        supervisor,
        event("Stop", tmp_path, stop_hook_active=False, last_assistant_message="Started it")
    )

    assert output["decision"] == "block"
    assert output["reason"] == "meaningful implementation work remains"
    session = store.load("thr-attached-123")
    assert session is not None and session.state is not None
    assert session.state.workers[0].status is WorkerStatus.RUNNING
    assert session.state.active_workers == ["attached-worker"]

    second = await handle(
        supervisor,
        event("Stop", tmp_path, stop_hook_active=True, last_assistant_message="Still incomplete")
    )
    assert second["continue"] is False
    assert "one automatic continuation" in second["systemMessage"]


@pytest.mark.asyncio
async def test_ready_stop_finishes_attached_worker(tmp_path) -> None:
    model = StubModel(
        implementation_complete=0.99,
        requirements_satisfied=0.99,
        ready_to_finish=0.99,
        tests_sufficient=0.99,
        needs_verification=0.0,
    )
    supervisor, store, _ = runtime(tmp_path, model)
    await handle(supervisor, event("UserPromptSubmit", tmp_path, prompt="Implement it"))

    assert await handle(supervisor, event("Stop", tmp_path)) == {}

    session = store.load("thr-attached-123")
    assert session is not None and session.state is not None
    assert session.state.status is FactoryStatus.FINISHED
    assert session.state.active_workers == []


@pytest.mark.asyncio
async def test_assessment_failure_denies_pre_tool_use(tmp_path) -> None:
    supervisor, _, _ = runtime(tmp_path, FailingModel())
    await handle(supervisor, event("UserPromptSubmit", tmp_path, prompt="Implement it"))

    output = await handle(
        supervisor,
        event(
            "PreToolUse",
            tmp_path,
            tool_name="Bash",
            tool_input={"command": "make deploy"},
        )
    )

    decision = output["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "Jev unavailable" in decision["permissionDecisionReason"]


@pytest.mark.asyncio
async def test_session_end_releases_attached_worker(tmp_path) -> None:
    supervisor, store, _ = runtime(tmp_path)
    await handle(supervisor, event("SessionStart", tmp_path, source="startup"))
    assert store.load("thr-attached-123") is not None

    assert await handle(supervisor, event("SessionEnd", tmp_path, reason="other")) == {}
    assert store.load("thr-attached-123") is None


def test_session_store_hashes_ids_and_purges_expired_records(tmp_path) -> None:
    store = AttachedSessionStore(tmp_path / "data", ttl_seconds=60)
    session = store.new("../unsafe/session", tmp_path)
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    store.save(session)

    path = store.path_for("../unsafe/session")
    assert path.parent == store.sessions_dir
    assert "unsafe" not in path.name
    assert store.purge_expired() == 1
    assert not path.exists()


@pytest.mark.asyncio
async def test_non_start_event_requires_a_routed_prompt(tmp_path) -> None:
    supervisor, _, _ = runtime(tmp_path)
    with pytest.raises(HookError, match="before work_submitted"):
        await handle(
            supervisor,
            event("PreToolUse", tmp_path, tool_name="Bash", tool_input={"command": "pwd"})
        )


@pytest.mark.asyncio
async def test_routing_failure_returns_codex_block_json(tmp_path) -> None:
    runtime_config = config()
    store = AttachedSessionStore(tmp_path / "data", ttl_seconds=60)
    supervisor = AttachedWorkerRuntime(
        model=StubModel(),
        router=FailingRouter(),
        responsibilities=configured_registry(runtime_config),
        config=runtime_config,
        store=store,
    )

    output = await handle(supervisor, event("UserPromptSubmit", tmp_path, prompt="Implement it"))

    assert output == {
        "decision": "block",
        "reason": "Foreman could not route this work: Jev router unavailable",
    }


def test_hook_cli_reads_and_writes_codex_json(tmp_path) -> None:
    runner = CliRunner()
    payload = event("SessionStart", tmp_path, source="startup")

    result = runner.invoke(
        app,
        ["hook", "--client", "codex", "--data-dir", str(tmp_path / "global-data")],
        input=json.dumps(payload),
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {}
    assert list((tmp_path / "global-data" / "sessions").glob("*.json"))


def test_hook_cli_defaults_to_codex_adapter(tmp_path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["hook", "--data-dir", str(tmp_path / "global-data")],
        input=json.dumps(event("SessionStart", tmp_path)),
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {}


def test_hook_cli_rejects_unknown_client_before_processing(tmp_path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["hook", "--client", "unknown", "--data-dir", str(tmp_path / "global-data")],
        input=json.dumps(event("SessionStart", tmp_path)),
    )

    assert result.exit_code == 2
    assert "unsupported hook client 'unknown'; available clients: codex" in result.stderr
    assert not (tmp_path / "global-data").exists()


def test_adapter_normalizes_codex_protocol() -> None:
    adapter = hook_adapter("CODEX")
    normalized = adapter.parse(
        {
            "session_id": "session-1",
            "cwd": ".",
            "hook_event_name": "PreToolUse",
            "turn_id": "turn-1",
            "tool_name": "Bash",
            "tool_input": {"command": "pwd"},
        }
    )

    assert normalized.client == "codex"
    assert normalized.kind.value == "before_tool"
    assert normalized.source_event_name == "PreToolUse"


def test_session_paths_are_namespaced_by_client(tmp_path) -> None:
    store = AttachedSessionStore(tmp_path / "data")

    assert store.path_for("same-id", "codex") != store.path_for("same-id", "other-client")


@pytest.mark.asyncio
async def test_runtime_accepts_normalized_events_from_another_client(tmp_path) -> None:
    supervisor, store, _ = runtime(tmp_path)
    normalized = HookEvent(
        client="other-client",
        session_id="session-1",
        cwd=str(tmp_path),
        kind=HookEventKind.WORK_SUBMITTED,
        source_event_name="message.received",
        prompt="Implement the feature",
    )

    outcome = await supervisor.handle(normalized)

    assert outcome.action is HookAction.INJECT_CONTEXT
    session = store.load("session-1", "other-client")
    assert session is not None and session.state is not None
    assert session.client == "other-client"
    assert session.state.workers[0].client == "other-client"
    assert session.state.workers[0].codex_thread_id is None


@pytest.mark.asyncio
async def test_attached_runtime_persists_extension_route_context(tmp_path) -> None:
    runtime_config = config()
    store = AttachedSessionStore(tmp_path / "data", ttl_seconds=60)
    supervisor = AttachedWorkerRuntime(
        model=StubModel(),
        router=StubRouter(),
        responsibilities=configured_registry(runtime_config),
        config=runtime_config,
        store=store,
        routing_groups=(
            RouteGroup(
                id="example.project",
                route=ResponsibilityRoute(always=True),
                responsibility_ids=("quality.documentation",),
                bindings={"project_id": "project-123"},
            ),
        ),
        active_extension_ids=("example",),
        extension_snapshot_revisions={"example": "revision-1"},
    )

    await handle(
        supervisor,
        event("UserPromptSubmit", tmp_path, prompt="Update the documentation"),
    )

    session = store.load("thr-attached-123")
    assert session is not None and session.state is not None
    assert session.state.routing_bindings == {
        "example.project": {"project_id": "project-123"}
    }
    assert session.state.active_extension_ids == ["example"]
    assert session.state.extension_snapshot_revisions == {"example": "revision-1"}


@pytest.mark.asyncio
async def test_attached_runtime_rejects_snapshot_change_between_hooks(tmp_path) -> None:
    supervisor, store, router = runtime(tmp_path)
    supervisor.active_extension_ids = ("example",)
    supervisor.extension_snapshot_revisions = {"example": "revision-1"}
    await handle(supervisor, event("UserPromptSubmit", tmp_path, prompt="Implement it"))

    changed = AttachedWorkerRuntime(
        model=StubModel(),
        router=router,
        responsibilities=configured_registry(config()),
        config=config(),
        store=store,
        active_extension_ids=("example",),
        extension_snapshot_revisions={"example": "revision-2"},
    )

    with pytest.raises(HookError, match="snapshots changed"):
        await handle(
            changed,
            event("PreToolUse", tmp_path, tool_name="Bash", tool_input={"command": "pwd"}),
        )

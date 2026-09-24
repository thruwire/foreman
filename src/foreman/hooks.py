from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from foreman.config import FactoryConfig
from foreman.foreman import ForemanModel, ForemanModelError
from foreman.models import (
    FactoryState,
    FactoryStatus,
    Intervention,
    InterventionType,
    WorkerRecord,
    WorkerStatus,
    WorkerType,
)
from foreman.observation import build_observation
from foreman.policy import FactoryPolicy
from foreman.responsibilities import ResponsibilityRegistry
from foreman.routing import (
    ResponsibilityRouter,
    ResponsibilityRoutingError,
    resolved_responsibility_ids,
)


class HookError(RuntimeError):
    """A hook event or attached-worker session could not be processed safely."""


class HookEventKind(StrEnum):
    """Assistant-neutral lifecycle events understood by Foreman."""

    SESSION_STARTED = "session_started"
    WORK_SUBMITTED = "work_submitted"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    WORKER_STOPPING = "worker_stopping"
    SESSION_ENDED = "session_ended"


class HookEvent(BaseModel):
    """Normalized event produced by a coding-assistant adapter."""

    model_config = ConfigDict(extra="forbid")

    client: str = Field(min_length=1, max_length=100)
    session_id: str = Field(min_length=1, max_length=1_000)
    cwd: str = Field(min_length=1)
    kind: HookEventKind
    source_event_name: str = Field(min_length=1)
    turn_id: str | None = None
    prompt: str | None = None
    tool_name: str | None = None
    tool_use_id: str | None = None
    tool_input: Any = None
    tool_response: Any = None
    continuation_active: bool = False
    last_assistant_message: str | None = None

    @model_validator(mode="after")
    def required_event_fields(self) -> HookEvent:
        if self.kind is HookEventKind.WORK_SUBMITTED and not (
            self.prompt and self.prompt.strip()
        ):
            raise ValueError("work_submitted requires a non-empty prompt")
        if (
            self.kind in {HookEventKind.BEFORE_TOOL, HookEventKind.AFTER_TOOL}
            and not self.tool_name
        ):
            raise ValueError(f"{self.kind.value} requires tool_name")
        return self


class HookAction(StrEnum):
    """Assistant-neutral effect requested by Foreman."""

    ALLOW = "allow"
    INJECT_CONTEXT = "inject_context"
    BLOCK = "block"
    HALT = "halt"


class HookOutcome(BaseModel):
    """A semantic hook result that an assistant adapter can render."""

    model_config = ConfigDict(extra="forbid")

    action: HookAction = HookAction.ALLOW
    reason: str | None = None
    system_message: str | None = None


class AttachedSession(BaseModel):
    """Bounded local state shared by hook calls from one interactive worker."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 2
    client: str = "codex"
    session_id: str
    repository: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    event_count: int = Field(default=0, ge=0)
    recent_events: list[dict[str, Any]] = Field(default_factory=list)
    state: FactoryState | None = None

    def touch(self, ttl_seconds: float) -> None:
        now = datetime.now(UTC)
        self.updated_at = now
        self.expires_at = now + timedelta(seconds=ttl_seconds)


def default_foreman_data_dir() -> Path:
    configured = os.getenv("FOREMAN_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".foreman").resolve()


class AttachedSessionStore:
    """Atomic, process-safe storage for human-initiated worker sessions."""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        *,
        ttl_seconds: float = 604_800.0,
        lock_timeout_seconds: float = 30.0,
    ) -> None:
        self.data_dir = (
            Path(data_dir).expanduser().resolve()
            if data_dir is not None
            else default_foreman_data_dir()
        )
        self.sessions_dir = self.data_dir / "sessions"
        self.locks_dir = self.sessions_dir / ".locks"
        self.ttl_seconds = ttl_seconds
        self.lock_timeout_seconds = lock_timeout_seconds

    @staticmethod
    def _key(session_id: str, client: str = "codex") -> str:
        identity = f"{client}\0{session_id}"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def path_for(self, session_id: str, client: str = "codex") -> Path:
        return self.sessions_dir / f"{self._key(session_id, client)}.json"

    def _prepare(self) -> None:
        self.locks_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.sessions_dir, 0o700)
            os.chmod(self.locks_dir, 0o700)
        except OSError:
            pass

    @contextmanager
    def locked(self, session_id: str, client: str = "codex") -> Iterator[None]:
        self._prepare()
        lock_path = self.locks_dir / f"{self._key(session_id, client)}.lock"
        deadline = time.monotonic() + self.lock_timeout_seconds
        stale_after = max(60.0, self.lock_timeout_seconds * 4)
        descriptor: int | None = None
        while descriptor is None:
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                os.write(descriptor, f"{os.getpid()}\n".encode())
            except FileExistsError:
                try:
                    age = time.time() - lock_path.stat().st_mtime
                    if age > stale_after:
                        lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise HookError(f"timed out locking hook session {session_id!r}") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            os.close(descriptor)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    def new(
        self,
        session_id: str,
        repository: Path,
        client: str = "codex",
    ) -> AttachedSession:
        now = datetime.now(UTC)
        return AttachedSession(
            client=client,
            session_id=session_id,
            repository=str(repository),
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
        )

    def load(self, session_id: str, client: str = "codex") -> AttachedSession | None:
        path = self.path_for(session_id, client)
        try:
            session = AttachedSession.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValidationError, ValueError) as error:
            raise HookError(f"hook session {session_id!r} is malformed: {error}") from error
        if session.session_id != session_id or session.client != client:
            raise HookError(f"hook session identity mismatch for {session_id!r}")
        if session.expires_at <= datetime.now(UTC):
            self.delete(session_id, client)
            return None
        return session

    def save(self, session: AttachedSession) -> None:
        self._prepare()
        target = self.path_for(session.session_id, session.client)
        payload = session.model_dump_json(indent=2)
        descriptor, temporary = tempfile.mkstemp(
            prefix="session-", suffix=".tmp", dir=self.sessions_dir
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

    def delete(self, session_id: str, client: str = "codex") -> None:
        try:
            self.path_for(session_id, client).unlink()
        except FileNotFoundError:
            pass

    def purge_expired(self) -> int:
        if not self.sessions_dir.exists():
            return 0
        now = datetime.now(UTC)
        removed = 0
        for path in self.sessions_dir.glob("*.json"):
            try:
                session = AttachedSession.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError, ValueError):
                continue
            if session.expires_at <= now:
                try:
                    path.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
        return removed


def _repository_root(cwd: str) -> Path:
    directory = Path(cwd).expanduser().resolve()
    if not directory.is_dir():
        raise HookError(f"hook cwd is not a directory: {directory}")
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return directory
    if result.returncode != 0 or not result.stdout.strip():
        return directory
    return Path(result.stdout.strip()).resolve()


def _bounded_json(value: Any, limit: int) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        rendered = repr(value)
    if len(rendered) <= limit:
        return rendered
    return f"[... {len(rendered) - limit} earlier characters omitted ...]{rendered[-limit:]}"


def _event_record(event: HookEvent, limit: int) -> dict[str, Any]:
    details: dict[str, Any] = {}
    if event.prompt is not None:
        details["prompt"] = event.prompt
    if event.tool_name is not None:
        details["tool_name"] = event.tool_name
    if event.tool_input is not None:
        details["tool_input"] = event.tool_input
    if event.tool_response is not None:
        details["tool_response"] = event.tool_response
    if event.last_assistant_message is not None:
        details["last_assistant_message"] = event.last_assistant_message
    return {
        "client": event.client,
        "event": event.kind.value,
        "source_event": event.source_event_name,
        "turn_id": event.turn_id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "summary": _bounded_json(details, limit),
    }


class AttachedWorkerRuntime:
    """Route and supervise normalized lifecycle events from an attached worker."""

    def __init__(
        self,
        *,
        model: ForemanModel,
        router: ResponsibilityRouter,
        responsibilities: ResponsibilityRegistry,
        config: FactoryConfig,
        store: AttachedSessionStore | None = None,
    ) -> None:
        self.model = model
        self.router = router
        self.candidates = responsibilities
        self.config = config
        self.store = store or AttachedSessionStore(ttl_seconds=config.hook_session_ttl_seconds)

    async def handle(self, event: HookEvent) -> HookOutcome:
        repository = _repository_root(event.cwd)
        self.store.purge_expired()
        with self.store.locked(event.session_id, event.client):
            if event.kind is HookEventKind.SESSION_ENDED:
                self.store.delete(event.session_id, event.client)
                return HookOutcome()

            session = self.store.load(event.session_id, event.client) or self.store.new(
                event.session_id, repository, event.client
            )
            session.repository = str(repository)
            session.event_count += 1
            session.recent_events.append(_event_record(event, self.config.output_limit))
            session.recent_events = session.recent_events[-self.config.event_history_limit :]

            if event.kind is HookEventKind.SESSION_STARTED:
                session.touch(self.store.ttl_seconds)
                self.store.save(session)
                return HookOutcome()
            if event.kind is HookEventKind.WORK_SUBMITTED:
                try:
                    output = await self._route_prompt(session, event)
                except ResponsibilityRoutingError as error:
                    output = HookOutcome(
                        action=HookAction.BLOCK,
                        reason=f"Foreman could not route this work: {error}",
                    )
            else:
                if session.state is None:
                    raise HookError(
                        f"{event.source_event_name} arrived before work_submitted"
                    )
                output = await self._assess_event(session, event)

            self._bound_state(session)
            session.touch(self.store.ttl_seconds)
            self.store.save(session)
            return output

    async def _route_prompt(
        self, session: AttachedSession, event: HookEvent
    ) -> HookOutcome:
        assert event.prompt is not None
        decision = await self.router.route(event.prompt.strip(), self.candidates)
        active_ids = resolved_responsibility_ids(decision, self.candidates)
        if not active_ids:
            raise ResponsibilityRoutingError("routing activated no responsibilities")

        state = session.state or FactoryState(
            run_id=(
                f"attached-{AttachedSessionStore._key(session.session_id, session.client)[:16]}"
            ),
            job=event.prompt.strip(),
            repository=session.repository,
            max_iterations=self.config.max_iterations,
            candidate_responsibility_ids=[
                responsibility.id for responsibility in self.candidates.responsibilities
            ],
        )
        state.job = event.prompt.strip()
        state.repository = session.repository
        state.status = FactoryStatus.RUNNING
        state.finished_at = None
        state.iteration = 0
        state.max_iterations = self.config.max_iterations
        state.active_responsibility_ids = active_ids
        state.routing_scores = dict(decision.scores)
        state.retry_count = 0
        state.consecutive_assessment_failures = 0
        state.verification_started = False
        state.verification_completed = False

        worker_id = "attached-worker"
        worker = WorkerRecord(
            worker_id=worker_id,
            worker_type=WorkerType.CODING,
            mission=event.prompt.strip(),
            status=WorkerStatus.RUNNING,
            started_at=datetime.now(UTC),
            client=session.client,
            client_session_id=session.session_id,
            client_turn_id=event.turn_id,
            supports_steering=True,
        )
        state.workers = [worker]
        state.active_workers = [worker_id]
        state.completed_workers = []
        state.failed_workers = []
        state.latest_result = None
        state.latest_intervention = None
        state.touch()
        session.state = state

        names = ", ".join(active_ids)
        return HookOutcome(
            action=HookAction.INJECT_CONTEXT,
            reason=f"Foreman attached. Active responsibilities: {names}.",
        )

    async def _assess_event(
        self, session: AttachedSession, event: HookEvent
    ) -> HookOutcome:
        state = session.state
        assert state is not None
        active = self.candidates.routed(state.active_responsibility_ids)
        worker = state.workers[0]
        worker.client_turn_id = event.turn_id or worker.client_turn_id
        summary = session.recent_events[-1]["summary"]
        worker.stdout = f"{worker.stdout}\n{event.kind.value}: {summary}".strip()
        if len(worker.stdout) > self.config.output_limit:
            worker.stdout = worker.stdout[-self.config.output_limit :]

        if event.kind is HookEventKind.WORKER_STOPPING:
            worker.status = WorkerStatus.COMPLETED
            worker.finished_at = datetime.now(UTC)
            state.active_workers = []
            state.completed_workers = [worker.worker_id]
        else:
            worker.status = WorkerStatus.RUNNING
            worker.finished_at = None
            state.active_workers = [worker.worker_id]
            state.completed_workers = []

        state.iteration += 1
        state.touch()
        observation = await build_observation(
            state,
            self.config,
            recent_events=session.recent_events,
            repository_instruction_files=active.repository_instruction_files(),
        )
        try:
            result = await self.model.assess(observation, active.checks())
        except ForemanModelError as error:
            state.errors.append(str(error))
            state.consecutive_assessment_failures += 1
            return self._assessment_failure_output(event, str(error))

        state.consecutive_assessment_failures = 0
        evaluated = FactoryPolicy(
            self.config,
            active,
            enforce_iteration_limit=False,
        ).evaluate(state, result)
        directive = evaluated.selected_directive
        assert directive is not None
        state.latest_result = evaluated
        state.result_history.append(evaluated)
        state.latest_intervention = directive
        state.intervention_history.append(directive)
        output = self._directive_output(event, directive, state, worker)
        state.touch()
        return output

    def _assessment_failure_output(self, event: HookEvent, reason: str) -> HookOutcome:
        message = f"Foreman could not evaluate this operation: {reason}"
        if event.kind in {HookEventKind.BEFORE_TOOL, HookEventKind.AFTER_TOOL}:
            return HookOutcome(action=HookAction.BLOCK, reason=message)
        if event.kind is HookEventKind.WORKER_STOPPING and not event.continuation_active:
            return HookOutcome(action=HookAction.BLOCK, reason=message)
        return HookOutcome(action=HookAction.HALT, reason=message, system_message=message)

    def _directive_output(
        self,
        event: HookEvent,
        directive: Intervention,
        state: FactoryState,
        worker: WorkerRecord,
    ) -> HookOutcome:
        action = directive.action
        reason = directive.reason
        if action is InterventionType.STEER_WORKER:
            worker.steer_count += 1
            worker.last_steered_at = datetime.now(UTC)

        if event.kind is HookEventKind.BEFORE_TOOL:
            if action in {InterventionType.ESCALATE, InterventionType.STOP_WORKER}:
                return HookOutcome(action=HookAction.BLOCK, reason=reason)
            if action is InterventionType.STEER_WORKER:
                return HookOutcome(action=HookAction.INJECT_CONTEXT, reason=reason)
            return HookOutcome()

        if event.kind is HookEventKind.AFTER_TOOL:
            if action is InterventionType.ESCALATE:
                state.status = FactoryStatus.ESCALATED
                state.finished_at = datetime.now(UTC)
                return HookOutcome(
                    action=HookAction.HALT,
                    reason=reason,
                    system_message=reason,
                )
            if action is InterventionType.STOP_WORKER:
                worker.status = WorkerStatus.STOPPED
                worker.finished_at = datetime.now(UTC)
                state.active_workers = []
                return HookOutcome(action=HookAction.BLOCK, reason=reason)
            if action is InterventionType.STEER_WORKER:
                return HookOutcome(action=HookAction.INJECT_CONTEXT, reason=reason)
            return HookOutcome()

        if event.kind is not HookEventKind.WORKER_STOPPING:
            return HookOutcome()
        if action is InterventionType.FINISH:
            state.status = FactoryStatus.FINISHED
            state.finished_at = datetime.now(UTC)
            return HookOutcome()
        if action is InterventionType.ESCALATE:
            state.status = FactoryStatus.ESCALATED
            state.finished_at = datetime.now(UTC)
            return HookOutcome(
                action=HookAction.HALT,
                reason=reason,
                system_message=reason,
            )
        if event.continuation_active:
            return HookOutcome(
                action=HookAction.HALT,
                reason=reason,
                system_message=(
                    "Foreman still found unfinished work after one automatic continuation: "
                    f"{reason}"
                ),
            )
        if action is InterventionType.START_VERIFIER:
            state.verification_started = True
        worker.status = WorkerStatus.RUNNING
        worker.finished_at = None
        state.active_workers = [worker.worker_id]
        state.completed_workers = []
        return HookOutcome(action=HookAction.BLOCK, reason=reason)

    def _bound_state(self, session: AttachedSession) -> None:
        state = session.state
        if state is None:
            return
        limit = self.config.worker_history_limit
        state.result_history = state.result_history[-limit:]
        state.intervention_history = state.intervention_history[-limit:]
        state.errors = state.errors[-limit:]

    async def close(self) -> None:
        try:
            await self.model.close()
        finally:
            await self.router.close()


async def run_hook(runtime: AttachedWorkerRuntime, event: HookEvent) -> HookOutcome:
    try:
        return await runtime.handle(event)
    finally:
        await runtime.close()

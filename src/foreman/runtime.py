from __future__ import annotations

import asyncio
import inspect
import re
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from foreman.config import FactoryConfig
from foreman.foreman import ForemanModel, ForemanModelError
from foreman.models import (
    EventType,
    FactoryEvent,
    FactoryState,
    FactoryStatus,
    Intervention,
    InterventionType,
    VerificationResult,
    WorkerRecord,
    WorkerStatus,
    WorkerType,
)
from foreman.observation import ObservationBuilder
from foreman.persistence import RunStore
from foreman.policy import FactoryPolicy
from foreman.steering import build_steering_message
from foreman.workers import CodexAppServerWorker, CodexWorker, OpenCodeWorker, Worker
from foreman.workers.codex import mission_for

EventSink = Callable[[FactoryEvent], object]
WorkerFactory = Callable[[WorkerType], Worker]

IMPORTANT_EVENTS = {
    EventType.WORKER_COMPLETED,
    EventType.WORKER_FAILED,
    EventType.WORKER_STOPPED,
    EventType.VERIFICATION_COMPLETED,
}


class FactoryRuntime:
    """Runs coding workers while independently observing, assessing, and intervening."""

    def __init__(
        self,
        *,
        repository: Path | str,
        job: str,
        model: ForemanModel,
        config: FactoryConfig | None = None,
        worker_factory: WorkerFactory | None = None,
        store: RunStore | None = None,
        run_id: str | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self.repository = Path(repository).resolve()
        if not self.repository.is_dir():
            raise ValueError(f"repository is not a directory: {self.repository}")
        if not job.strip():
            raise ValueError("job cannot be empty")
        self.config = config or FactoryConfig.from_environment()
        self.model = model
        self.store = store or RunStore(self.repository)
        self.policy = FactoryPolicy(self.config)
        self.observer = ObservationBuilder(self.store, self.config)
        self.event_sink = event_sink
        self.queue: asyncio.Queue[FactoryEvent] = asyncio.Queue()
        self.worker_factory = worker_factory or self._default_worker_factory
        self.state = FactoryState(
            run_id=run_id or uuid4().hex[:12],
            job=job.strip(),
            repository=str(self.repository),
            max_iterations=self.config.max_iterations,
        )
        self._workers: dict[str, Worker] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._closed = False

    def _default_worker_factory(self, worker_type: WorkerType) -> Worker:
        del worker_type
        if self.config.worker_backend == "opencode":
            return OpenCodeWorker(
                output_limit=self.config.output_limit,
                graceful_termination_seconds=self.config.graceful_termination_seconds,
            )
        if self.config.codex_backend == "app-server":
            return CodexAppServerWorker(
                output_limit=self.config.output_limit,
                graceful_termination_seconds=self.config.graceful_termination_seconds,
            )
        return CodexWorker(
            output_limit=self.config.output_limit,
            graceful_termination_seconds=self.config.graceful_termination_seconds,
        )

    async def emit(
        self,
        event_type: EventType,
        payload: dict[str, object] | None = None,
        *,
        notify_foreman: bool = True,
    ) -> FactoryEvent:
        event = FactoryEvent(
            run_id=self.state.run_id,
            event_type=event_type,
            payload=payload or {},
        )
        self.store.append_event(event)
        if self.event_sink is not None:
            result = self.event_sink(event)
            if inspect.isawaitable(result):
                await result
        # Supervisor-originated events skip the queue so an assessment cannot trigger itself.
        if notify_foreman:
            await self.queue.put(event)
        return event

    def _worker(self, worker_id: str) -> WorkerRecord:
        return next(worker for worker in self.state.workers if worker.worker_id == worker_id)

    async def _worker_emit(
        self, worker_id: str, event_type: EventType, payload: dict[str, object]
    ) -> None:
        payload.setdefault("worker_id", worker_id)
        self.state.touch()
        self.store.save_state(self.state)
        await self.emit(event_type, payload)

    async def _run_worker(self, record: WorkerRecord, implementation: Worker) -> None:
        async def callback(event_type: EventType, payload: dict[str, object]) -> None:
            await self._worker_emit(record.worker_id, event_type, payload)

        try:
            await implementation.run(
                record,
                self.repository,
                callback,
                self.config.worker_timeout_seconds,
            )
        except asyncio.CancelledError:
            if record.status is WorkerStatus.RUNNING:
                record.status = WorkerStatus.CANCELLED
                record.finished_at = datetime.now(UTC)
                record.termination_reason = "cancelled"
            raise
        except Exception as error:
            record.status = WorkerStatus.FAILED
            record.finished_at = datetime.now(UTC)
            record.termination_reason = "worker_exception"
            record.stderr = f"{record.stderr}\n{type(error).__name__}: {error}".strip()
        finally:
            if record.worker_id in self.state.active_workers:
                self.state.active_workers.remove(record.worker_id)
            if record.status is WorkerStatus.COMPLETED:
                self.state.completed_workers.append(record.worker_id)
            elif record.status in {
                WorkerStatus.FAILED,
                WorkerStatus.TIMED_OUT,
                WorkerStatus.CANCELLED,
            }:
                self.state.failed_workers.append(record.worker_id)
                self.state.errors.append(
                    f"{record.worker_id}: {record.termination_reason or record.status.value}"
                )

            if record.worker_type is WorkerType.VERIFIER:
                self.state.verification_completed = True
                passed = record.status is WorkerStatus.COMPLETED
                self.state.verification_results.append(
                    VerificationResult(
                        worker_id=record.worker_id,
                        passed=passed,
                        summary=(record.stdout or record.stderr)[-self.config.output_limit :],
                    )
                )

            self.state.touch()
            self.store.save_state(self.state)
            if record.worker_type is WorkerType.VERIFIER:
                terminal_type = EventType.VERIFICATION_COMPLETED
            elif record.status is WorkerStatus.COMPLETED:
                terminal_type = EventType.WORKER_COMPLETED
            elif record.status is WorkerStatus.STOPPED:
                terminal_type = EventType.WORKER_STOPPED
            else:
                terminal_type = EventType.WORKER_FAILED
            await self.emit(
                terminal_type,
                {
                    "worker_id": record.worker_id,
                    "worker_type": record.worker_type.value,
                    "status": record.status.value,
                    "exit_code": record.exit_code,
                    "termination_reason": record.termination_reason,
                },
            )
            if record.worker_type is WorkerType.CODING and record.status is WorkerStatus.COMPLETED:
                await self._verify_tests_after_worker(record)

    async def _verify_tests_after_worker(self, record: WorkerRecord) -> None:
        """Run the repository's pytest suite after a coding worker completes
        and emit a TEST_RESULT event with the parsed summary.

        Backends with buffered/silent output (hermes on Windows) otherwise
        leave the supervisor without any executed-test evidence, so
        tests_sufficient can never converge. The run is bounded by
        test_command_timeout and failures are non-fatal: the hook is
        evidence-gathering, not a gate.
        """
        if not self.config.verify_tests_on_complete:
            return
        pytest_exe = Path(self.repository) / ".venv" / "Scripts" / "python.exe"
        if not pytest_exe.exists():
            pytest_exe = Path(sys.executable)
        command = [str(pytest_exe), "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=self.repository,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                stdin=asyncio.subprocess.DEVNULL,
            )
        except Exception as error:
            await self._worker_emit(
                record.worker_id,
                EventType.TEST_RESULT,
                {"error": f"test verification launch failed: {error}"},
            )
            return
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), self.config.test_command_timeout)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await self._worker_emit(
                record.worker_id,
                EventType.TEST_RESULT,
                {"error": f"test verification timed out after {self.config.test_command_timeout}s"},
            )
            return
        output = stdout.decode("utf-8", errors="replace")
        summary = ""
        passed = failed = 0
        for line in output.splitlines():
            match = re.search(r"(\d+)\s+passed(?:,\s*(\d+)\s+failed)?", line)
            if match:
                passed = int(match.group(1))
                failed = int(match.group(2) or 0)
                summary = line.strip()[:200]
                break
        await self._worker_emit(
            record.worker_id,
            EventType.TEST_RESULT,
            {
                "worker_id": record.worker_id,
                "source": "foreman_verification",
                "passed": passed,
                "failed": failed,
                "summary": summary or "no pytest summary line found",
            },
        )

    async def start_worker(self, worker_type: WorkerType) -> WorkerRecord:
        if len(self.state.active_workers) >= self.config.max_concurrent_workers:
            raise RuntimeError("maximum concurrent workers reached")
        if len(self.state.workers) >= self.config.max_workers:
            raise RuntimeError("maximum workers per job reached")
        number = len(self.state.workers) + 1
        worker_id = f"worker-{number}"
        implementation = self.worker_factory(worker_type)
        record = WorkerRecord(
            worker_id=worker_id,
            worker_type=worker_type,
            mission=mission_for(worker_type, self.state.job),
            attempt=self.state.retry_count + 1,
            supports_steering=getattr(implementation, "supports_steering", False),
        )
        self.state.workers.append(record)
        self.state.active_workers.append(worker_id)
        if worker_type is WorkerType.VERIFIER:
            self.state.verification_started = True
        self.state.touch()
        self.store.save_state(self.state)
        self._workers[worker_id] = implementation
        self._tasks[worker_id] = asyncio.create_task(
            self._run_worker(record, implementation), name=f"foreman-{worker_id}"
        )
        event_type = (
            EventType.VERIFICATION_STARTED
            if worker_type is WorkerType.VERIFIER
            else EventType.WORKER_STARTED
        )
        await self.emit(
            event_type,
            {
                "worker_id": worker_id,
                "worker_type": worker_type.value,
                "attempt": record.attempt,
            },
        )
        return record

    async def _assess(self) -> Intervention | None:
        self.state.iteration += 1
        self.state.touch()
        observation = await self.observer.build(self.state)
        await self.emit(
            EventType.FOREMAN_OBSERVED,
            {"iteration": self.state.iteration},
            notify_foreman=False,
        )
        try:
            assessment = await self.model.assess(observation)
        except ForemanModelError as error:
            self.state.errors.append(str(error))
            self.state.consecutive_assessment_failures += 1
            failures = self.state.consecutive_assessment_failures
            budget = self.config.max_consecutive_assessment_failures
            if failures < budget:
                # Tolerate transient supervisor outages: keep the workers
                # running and retry the assessment on the next cycle.
                intervention = Intervention(
                    action=InterventionType.CONTINUE,
                    reason=f"semantic assessment failed ({failures}/{budget} tolerated): {error}",
                    assessment_iteration=self.state.iteration,
                )
            else:
                intervention = Intervention(
                    action=InterventionType.ESCALATE,
                    reason=f"semantic assessment unavailable: {error}",
                    assessment_iteration=self.state.iteration,
                )
            self.state.latest_intervention = intervention
            self.state.intervention_history.append(intervention)
            self.state.touch()
            self.store.save_state(self.state)
            await self.emit(
                EventType.FOREMAN_INTERVENED,
                intervention.model_dump(mode="json"),
                notify_foreman=False,
            )
            return intervention

        self.state.consecutive_assessment_failures = 0
        self.state.latest_assessment = assessment
        self.state.assessment_history.append(assessment)
        await self.emit(
            EventType.FOREMAN_ASSESSED,
            {"iteration": self.state.iteration, "assessment": assessment.model_dump(mode="json")},
            notify_foreman=False,
        )
        intervention = self.policy.decide(self.state, assessment)
        self.state.latest_intervention = intervention
        self.state.intervention_history.append(intervention)
        self.state.touch()
        self.store.save_state(self.state)
        await self.emit(
            EventType.FOREMAN_INTERVENED,
            intervention.model_dump(mode="json"),
            notify_foreman=False,
        )
        return intervention

    async def _apply(self, intervention: Intervention) -> None:
        action = intervention.action
        if action is InterventionType.CONTINUE:
            return
        if action is InterventionType.START_WORKER:
            await self.start_worker(WorkerType.CODING)
            return
        if action is InterventionType.START_VERIFIER:
            await self.start_worker(WorkerType.VERIFIER)
            return
        if action is InterventionType.STEER_WORKER:
            worker_id = intervention.worker_id or (
                self.state.active_workers[0] if self.state.active_workers else None
            )
            if worker_id is None or worker_id not in self._workers:
                return
            record = self._worker(worker_id)
            assessment = self.state.latest_assessment
            if assessment is None:
                return
            message = build_steering_message(assessment)
            record.steer_count += 1
            record.steering_history.append(message)
            steered = await self._workers[worker_id].steer(message)
            if steered:
                record.last_steered_at = datetime.now(UTC)
                event_type = EventType.WORKER_STEERED
            else:
                event_type = EventType.WORKER_STEER_FAILED
                self.state.errors.append(f"{worker_id}: active-turn steering was not accepted")
            self.state.touch()
            self.store.save_state(self.state)
            await self.emit(
                event_type,
                {
                    "worker_id": worker_id,
                    "message": message,
                    "assessment_iteration": intervention.assessment_iteration,
                },
                notify_foreman=False,
            )
            return
        if action is InterventionType.STOP_WORKER:
            worker_id = intervention.worker_id or (
                self.state.active_workers[0] if self.state.active_workers else None
            )
            if worker_id and worker_id in self._workers:
                await self._workers[worker_id].terminate(intervention.reason)
            return
        if action is InterventionType.RETRY_WORKER:
            self.state.retry_count += 1
            await self.start_worker(WorkerType.CODING)
            return
        if action is InterventionType.FINISH:
            self.state.status = FactoryStatus.FINISHED
            self.state.finished_at = datetime.now(UTC)
            self.state.touch()
            self.store.save_state(self.state)
            await self.emit(
                EventType.FACTORY_FINISHED,
                {"result": self.state.status.value},
                notify_foreman=False,
            )
            return
        if action is InterventionType.ESCALATE:
            await self._terminate_active("factory escalated")
            self.state.status = FactoryStatus.ESCALATED
            self.state.finished_at = datetime.now(UTC)
            self.state.touch()
            self.store.save_state(self.state)
            await self.emit(
                EventType.FACTORY_ESCALATED,
                {"reason": intervention.reason},
                notify_foreman=False,
            )

    async def _watch_loop(self) -> None:
        last_assessment = -float("inf")
        dirty = False
        force = False
        while self.state.status is FactoryStatus.RUNNING:
            now = time.monotonic()
            since = now - last_assessment
            min_remaining = max(0.0, self.config.assessment_min_interval_seconds - since)
            periodic_remaining = max(0.0, self.config.periodic_assessment_seconds - since)
            timeout = min(periodic_remaining, min_remaining) if dirty else periodic_remaining
            try:
                event = await asyncio.wait_for(self.queue.get(), timeout=max(0.001, timeout))
                dirty = True
                force = force or event.event_type in IMPORTANT_EVENTS
                if event.event_type is EventType.WORKER_STARTED and self.state.iteration == 0:
                    force = True
                # Collapse bursts such as JSONL streaming into one semantic observation.
                while not self.queue.empty():
                    event = self.queue.get_nowait()
                    force = force or event.event_type in IMPORTANT_EVENTS
            except TimeoutError:
                dirty = True

            now = time.monotonic()
            # Lifecycle boundaries bypass debounce; routine output respects the configured floor.
            interval_elapsed = now - last_assessment >= self.config.assessment_min_interval_seconds
            eligible = force or (dirty and interval_elapsed)
            if not eligible:
                continue
            intervention = await self._assess()
            last_assessment = time.monotonic()
            dirty = False
            force = False
            if intervention is not None:
                await self._apply(intervention)

    async def run(self) -> FactoryState:
        self.store.initialize(self.state)
        self.state.status = FactoryStatus.RUNNING
        self.state.touch()
        self.store.save_state(self.state)
        await self.emit(
            EventType.FACTORY_STARTED,
            {"job": self.state.job, "repository": self.state.repository},
            notify_foreman=False,
        )
        await self.start_worker(WorkerType.CODING)
        try:
            await asyncio.wait_for(self._watch_loop(), timeout=self.config.overall_timeout_seconds)
        except TimeoutError:
            await self._terminate_active("overall job timeout")
            self.state.status = FactoryStatus.FAILED
            self.state.finished_at = datetime.now(UTC)
            self.state.errors.append("overall job timeout")
            self.state.touch()
            self.store.save_state(self.state)
            await self.emit(
                EventType.FACTORY_FAILED,
                {"reason": "overall job timeout"},
                notify_foreman=False,
            )
        except asyncio.CancelledError:
            await self._terminate_active("factory cancelled")
            self.state.status = FactoryStatus.CANCELLED
            self.state.finished_at = datetime.now(UTC)
            self.state.touch()
            self.store.save_state(self.state)
            raise
        finally:
            await self.close()
        return self.state

    async def _terminate_active(self, reason: str) -> None:
        await asyncio.gather(
            *(
                self._workers[worker_id].terminate(reason)
                for worker_id in list(self.state.active_workers)
                if worker_id in self._workers
            ),
            return_exceptions=True,
        )
        pending = [task for task in self._tasks.values() if not task.done()]
        if pending:
            _, still_pending = await asyncio.wait(
                pending, timeout=self.config.graceful_termination_seconds + 0.5
            )
            for task in still_pending:
                task.cancel()
            if still_pending:
                await asyncio.gather(*still_pending, return_exceptions=True)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.state.active_workers:
            await self._terminate_active("factory shutdown")
        await self.model.close()

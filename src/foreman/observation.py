from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from foreman.config import FactoryConfig
from foreman.models import FactoryAssessment, FactoryState, Intervention, WorkerRecord
from foreman.persistence import RunStore


class FactoryObservation(BaseModel):
    """A compact, bounded snapshot of the evidence visible to Foreman."""

    model_config = ConfigDict(extra="forbid")

    original_job: str
    run_id: str
    factory_status: str
    iteration: int = Field(ge=0)
    active_workers: list[dict[str, Any]]
    worker_history: list[dict[str, Any]]
    latest_worker_output: str
    worker_exit_status: dict[str, int | None]
    worker_elapsed_seconds: dict[str, float]
    git_status: str
    git_diff: str
    changed_files: list[str]
    test_results: list[dict[str, Any]]
    verification_results: list[dict[str, Any]]
    recent_events: list[dict[str, Any]]
    previous_assessment: FactoryAssessment | None
    previous_intervention: Intervention | None
    attempts: int = Field(ge=0)
    failures: list[str]
    elapsed_factory_seconds: float = Field(ge=0.0)

    @field_validator(
        "original_job", "latest_worker_output", "git_status", "git_diff", mode="before"
    )
    @classmethod
    def strings_only(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("observation text fields must be strings")
        return value


def _tail(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"[... {len(value) - limit} earlier characters omitted ...]\n{value[-limit:]}"


def _bounded_worker(worker: WorkerRecord, output_limit: int) -> dict[str, Any]:
    return {
        "worker_id": worker.worker_id,
        "worker_type": worker.worker_type.value,
        "status": worker.status.value,
        "attempt": worker.attempt,
        "started_at": worker.started_at.isoformat() if worker.started_at else None,
        "finished_at": worker.finished_at.isoformat() if worker.finished_at else None,
        "elapsed_seconds": worker.duration_seconds,
        "exit_code": worker.exit_code,
        "stdout_tail": _tail(worker.stdout, output_limit),
        "stderr_tail": _tail(worker.stderr, output_limit),
        "termination_reason": worker.termination_reason,
        "codex_thread_id": worker.codex_thread_id,
        "codex_turn_id": worker.codex_turn_id,
        "steer_count": worker.steer_count,
        "last_steered_at": (
            worker.last_steered_at.isoformat() if worker.last_steered_at else None
        ),
        "steering_history": worker.steering_history[-3:],
    }


async def _git(repository: Path, *args: str, limit: int) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(repository),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5.0)
    except (FileNotFoundError, TimeoutError):
        return ""
    output = stdout if process.returncode == 0 else stderr
    return _tail(output.decode("utf-8", errors="replace"), limit)


class ObservationBuilder:
    def __init__(self, store: RunStore, config: FactoryConfig) -> None:
        self.store = store
        self.config = config

    async def build(self, state: FactoryState) -> FactoryObservation:
        repository = Path(state.repository)
        # Git evidence is independent, so gather it without serial subprocess latency.
        status_task = asyncio.create_task(
            _git(repository, "status", "--short", limit=self.config.field_limit)
        )
        diff_task = asyncio.create_task(
            _git(repository, "diff", "--no-ext-diff", limit=self.config.diff_limit)
        )
        names_task = asyncio.create_task(
            _git(repository, "diff", "--name-only", limit=self.config.field_limit)
        )
        git_status, git_diff, names = await asyncio.gather(status_task, diff_task, names_task)

        active = [worker for worker in state.workers if worker.worker_id in state.active_workers]
        history = state.workers[-self.config.worker_history_limit :]
        latest = history[-1] if history else None
        elapsed = max(0.0, (datetime.now(UTC) - state.started_at).total_seconds())

        return FactoryObservation(
            original_job=_tail(state.job, self.config.field_limit),
            run_id=state.run_id,
            factory_status=state.status.value,
            iteration=state.iteration,
            active_workers=[
                _bounded_worker(worker, self.config.output_limit) for worker in active
            ],
            worker_history=[
                _bounded_worker(worker, self.config.output_limit) for worker in history
            ],
            latest_worker_output=(
                _tail(f"{latest.stdout}\n{latest.stderr}", self.config.output_limit)
                if latest
                else ""
            ),
            worker_exit_status={worker.worker_id: worker.exit_code for worker in history},
            worker_elapsed_seconds={
                worker.worker_id: worker.duration_seconds or 0.0 for worker in active
            },
            git_status=git_status,
            git_diff=git_diff,
            changed_files=[line for line in names.splitlines() if line][
                : self.config.worker_history_limit * 10
            ],
            test_results=[],
            verification_results=[
                result.model_dump(mode="json") for result in state.verification_results
            ],
            recent_events=self.store.recent_event_dicts(
                state.run_id, self.config.event_history_limit
            ),
            previous_assessment=state.latest_assessment,
            previous_intervention=state.latest_intervention,
            attempts=len(state.workers),
            failures=state.errors[-self.config.worker_history_limit :],
            elapsed_factory_seconds=elapsed,
        )

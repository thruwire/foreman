from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class WorkerType(StrEnum):
    CODING = "coding"
    VERIFIER = "verifier"


class WorkerStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class WorkerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1)
    worker_type: WorkerType
    mission: str = Field(min_length=1, max_length=50_000)
    status: WorkerStatus = WorkerStatus.PENDING
    attempt: int = Field(default=1, ge=1)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    termination_reason: str | None = None
    codex_thread_id: str | None = None
    codex_turn_id: str | None = None
    steer_count: int = Field(default=0, ge=0)
    last_steered_at: datetime | None = None
    steering_history: list[str] = Field(default_factory=list)

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at or datetime.now(UTC)
        return max(0.0, (end - self.started_at).total_seconds())

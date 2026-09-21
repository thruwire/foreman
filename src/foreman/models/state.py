from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from foreman.models.assessment import FactoryAssessment
from foreman.models.events import Intervention
from foreman.models.worker import WorkerRecord


class FactoryStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str
    passed: bool
    summary: str = ""
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FactoryState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    job: str = Field(min_length=1, max_length=100_000)
    repository: str = Field(min_length=1)
    status: FactoryStatus = FactoryStatus.CREATED
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=20, ge=1)
    workers: list[WorkerRecord] = Field(default_factory=list)
    active_workers: list[str] = Field(default_factory=list)
    completed_workers: list[str] = Field(default_factory=list)
    failed_workers: list[str] = Field(default_factory=list)
    latest_assessment: FactoryAssessment | None = None
    assessment_history: list[FactoryAssessment] = Field(default_factory=list)
    latest_intervention: Intervention | None = None
    intervention_history: list[Intervention] = Field(default_factory=list)
    verification_results: list[VerificationResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    retry_count: int = Field(default=0, ge=0)
    consecutive_assessment_failures: int = Field(default=0, ge=0)
    verification_started: bool = False
    verification_completed: bool = False
    finish_thresholds_met: bool = False
    smoothed_scores: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_worker_references(self) -> FactoryState:
        ids = [worker.worker_id for worker in self.workers]
        if len(ids) != len(set(ids)):
            raise ValueError("worker ids must be unique")
        known = set(ids)
        referenced = set(self.active_workers + self.completed_workers + self.failed_workers)
        if not referenced <= known:
            raise ValueError("worker status lists must reference known workers")
        if len(self.active_workers) != len(set(self.active_workers)):
            raise ValueError("active_workers contains duplicates")
        return self

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)

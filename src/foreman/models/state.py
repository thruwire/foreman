from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from foreman.models.events import Intervention
from foreman.models.result import ForemanResult
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

    schema_version: int = Field(default=4, ge=4)
    run_id: str = Field(min_length=1)
    job: str = Field(min_length=1, max_length=100_000)
    repository: str = Field(min_length=1)
    status: FactoryStatus = FactoryStatus.CREATED
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=20, ge=1)
    candidate_responsibility_ids: list[str] = Field(default_factory=list)
    active_responsibility_ids: list[str] = Field(default_factory=list)
    routing_scores: dict[str, Annotated[float, Field(ge=0.0, le=1.0)]] = Field(default_factory=dict)
    routing_bindings: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    routing_trace: list[dict[str, JsonValue]] = Field(default_factory=list)
    active_extension_ids: list[str] = Field(default_factory=list)
    extension_snapshot_revisions: dict[str, str] = Field(default_factory=dict)
    workers: list[WorkerRecord] = Field(default_factory=list)
    active_workers: list[str] = Field(default_factory=list)
    completed_workers: list[str] = Field(default_factory=list)
    failed_workers: list[str] = Field(default_factory=list)
    latest_result: ForemanResult | None = None
    result_history: list[ForemanResult] = Field(default_factory=list)
    latest_intervention: Intervention | None = None
    intervention_history: list[Intervention] = Field(default_factory=list)
    verification_results: list[VerificationResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    retry_count: int = Field(default=0, ge=0)
    consecutive_assessment_failures: int = Field(default=0, ge=0)
    verification_started: bool = False
    verification_completed: bool = False

    @model_validator(mode="after")
    def validate_worker_references(self) -> FactoryState:
        if len(self.candidate_responsibility_ids) != len(set(self.candidate_responsibility_ids)):
            raise ValueError("candidate_responsibility_ids contains duplicates")
        if len(self.active_responsibility_ids) != len(set(self.active_responsibility_ids)):
            raise ValueError("active_responsibility_ids contains duplicates")
        if len(self.active_extension_ids) != len(set(self.active_extension_ids)):
            raise ValueError("active_extension_ids contains duplicates")
        candidates = set(self.candidate_responsibility_ids)
        if not set(self.active_responsibility_ids) <= candidates:
            raise ValueError("active responsibilities must be routing candidates")
        if not set(self.routing_scores) <= candidates:
            raise ValueError("routing scores must reference routing candidates")

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

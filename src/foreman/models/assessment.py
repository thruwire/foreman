from __future__ import annotations

from datetime import UTC, datetime
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, field_validator

from foreman.models.result import ForemanResult


class FactoryAssessment(BaseModel):
    """Legacy flat assessment retained for persisted-run and API compatibility."""

    model_config = ConfigDict(extra="forbid")

    implementation_complete: float = Field(ge=0.0, le=1.0)
    tests_sufficient: float = Field(ge=0.0, le=1.0)
    requirements_satisfied: float = Field(ge=0.0, le=1.0)
    needs_verification: float = Field(ge=0.0, le=1.0)
    meaningful_progress: float = Field(ge=0.0, le=1.0)
    worker_stuck: float = Field(ge=0.0, le=1.0)
    work_off_track: float = Field(ge=0.0, le=1.0)
    agents_md_drift: float = Field(default=0.0, ge=0.0, le=1.0)
    ready_to_finish: float = Field(ge=0.0, le=1.0)
    needs_human: float = Field(ge=0.0, le=1.0)
    assessed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(
        "implementation_complete",
        "tests_sufficient",
        "requirements_satisfied",
        "needs_verification",
        "meaningful_progress",
        "worker_stuck",
        "work_off_track",
        "agents_md_drift",
        "ready_to_finish",
        "needs_human",
    )
    @classmethod
    def finite_scores(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("assessment scores must be finite")
        return value

    def to_result(self) -> ForemanResult:
        """Convert the legacy flat shape into responsibility-owned checks."""

        return ForemanResult(
            checks={
                "core.completion": {
                    "implementation_complete": self.implementation_complete,
                    "requirements_satisfied": self.requirements_satisfied,
                    "ready_to_finish": self.ready_to_finish,
                },
                "core.verification": {
                    "tests_sufficient": self.tests_sufficient,
                    "needs_verification": self.needs_verification,
                },
                "core.worker-health": {
                    "meaningful_progress": self.meaningful_progress,
                    "worker_stuck": self.worker_stuck,
                    "work_off_track": self.work_off_track,
                },
                "repository.instructions": {
                    "agents_md_drift": self.agents_md_drift,
                },
                "core.human-escalation": {"needs_human": self.needs_human},
            },
            evaluated_at=self.assessed_at,
        )

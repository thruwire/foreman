from __future__ import annotations

from datetime import UTC, datetime
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FactoryAssessment(BaseModel):
    """Jev's normalized semantic view of the job and current factory floor."""

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

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    FACTORY_STARTED = "FACTORY_STARTED"
    EXTENSIONS_ACTIVATED = "EXTENSIONS_ACTIVATED"
    FOREMAN_ROUTED = "FOREMAN_ROUTED"
    WORKER_STARTED = "WORKER_STARTED"
    WORKER_OUTPUT = "WORKER_OUTPUT"
    WORKER_STEERED = "WORKER_STEERED"
    WORKER_STEER_FAILED = "WORKER_STEER_FAILED"
    WORKER_COMPLETED = "WORKER_COMPLETED"
    WORKER_FAILED = "WORKER_FAILED"
    WORKER_STOPPED = "WORKER_STOPPED"
    REPOSITORY_CHANGED = "REPOSITORY_CHANGED"
    TEST_RESULT = "TEST_RESULT"
    FOREMAN_OBSERVED = "FOREMAN_OBSERVED"
    FOREMAN_ASSESSED = "FOREMAN_ASSESSED"
    FOREMAN_INTERVENED = "FOREMAN_INTERVENED"
    FOREMAN_DECIDED = "FOREMAN_DECIDED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"
    FACTORY_FINISHED = "FACTORY_FINISHED"
    FACTORY_ESCALATED = "FACTORY_ESCALATED"
    FACTORY_FAILED = "FACTORY_FAILED"


class InterventionType(StrEnum):
    CONTINUE = "CONTINUE"
    START_WORKER = "START_WORKER"
    START_VERIFIER = "START_VERIFIER"
    STEER_WORKER = "STEER_WORKER"
    STOP_WORKER = "STOP_WORKER"
    RETRY_WORKER = "RETRY_WORKER"
    FINISH = "FINISH"
    ESCALATE = "ESCALATE"


class Intervention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: InterventionType
    reason: str = Field(min_length=1, max_length=2_000)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    assessment_iteration: int = Field(ge=1)
    worker_id: str | None = None
    responsibility_id: str = "foreman.runtime"
    priority: int = 0
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


# A directive is an intervention proposed by a responsibility. The compatibility
# name remains public because persisted runs and existing embedders use it.
Directive = Intervention


class FactoryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str = Field(min_length=1)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)

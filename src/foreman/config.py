from __future__ import annotations

import os
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from foreman.models import AbstainCategory


def _environment_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean environment value: {value!r}")


def _environment_abstain_categories(value: str) -> list[AbstainCategory]:
    """Parse a comma-separated FOREMAN_ALWAYS_ABSTAIN value."""

    categories: list[AbstainCategory] = []
    for raw in value.split(","):
        name = raw.strip().lower()
        if not name:
            continue
        try:
            categories.append(AbstainCategory(name))
        except ValueError as error:
            valid = ", ".join(category.value for category in AbstainCategory)
            raise ValueError(
                f"invalid abstain category {raw!r}; expected one of: {valid}"
            ) from error
    return categories


class FactoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_min_interval_seconds: float = Field(default=5.0, ge=0.0)
    periodic_assessment_seconds: float = Field(default=30.0, gt=0.0)
    jev_timeout_seconds: float = Field(default=10.0, gt=0.0)
    worker_timeout_seconds: float = Field(default=3_600.0, gt=0.0)
    overall_timeout_seconds: float = Field(default=7_200.0, gt=0.0)
    graceful_termination_seconds: float = Field(default=5.0, ge=0.0)
    max_concurrent_workers: int = Field(default=1, ge=1)
    max_workers: int = Field(default=3, ge=1)
    max_retries: int = Field(default=1, ge=0)
    max_iterations: int = Field(default=20, ge=1)
    worker_backend: Literal["codex", "opencode"] = "codex"
    max_consecutive_assessment_failures: int = Field(default=3, ge=1)
    codex_backend: Literal["app-server", "exec"] = "app-server"
    steering_enabled: bool = True
    max_steers_per_worker: int = Field(default=1, ge=0)
    steering_grace_seconds: float = Field(default=30.0, ge=0.0)

    human_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    off_track_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    agents_drift_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    stuck_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    decision_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    always_abstain: list[AbstainCategory] = Field(
        default_factory=lambda: list(AbstainCategory)
    )
    verification_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    finish_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    requirements_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    tests_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    implementation_for_verification_threshold: float = Field(default=0.75, ge=0.0, le=1.0)

    diff_limit: int = Field(default=20_000, ge=100)
    output_limit: int = Field(default=12_000, ge=100)
    field_limit: int = Field(default=50_000, ge=100)
    event_history_limit: int = Field(default=30, ge=1)
    worker_history_limit: int = Field(default=10, ge=1)

    @classmethod
    def from_environment(cls) -> FactoryConfig:
        mapping: dict[str, tuple[str, Callable[[str], object]]] = {
            "FOREMAN_ASSESSMENT_MIN_INTERVAL_SECONDS": (
                "assessment_min_interval_seconds",
                float,
            ),
            "FOREMAN_PERIODIC_ASSESSMENT_SECONDS": ("periodic_assessment_seconds", float),
            "FOREMAN_JEV_TIMEOUT_SECONDS": ("jev_timeout_seconds", float),
            "FOREMAN_WORKER_TIMEOUT_SECONDS": ("worker_timeout_seconds", float),
            "FOREMAN_OVERALL_TIMEOUT_SECONDS": ("overall_timeout_seconds", float),
            "FOREMAN_MAX_WORKERS": ("max_workers", int),
            "FOREMAN_MAX_RETRIES": ("max_retries", int),
            "FOREMAN_MAX_ITERATIONS": ("max_iterations", int),
            "FOREMAN_WORKER_BACKEND": ("worker_backend", str),
            "FOREMAN_MAX_CONSECUTIVE_ASSESSMENT_FAILURES": (
                "max_consecutive_assessment_failures",
                int,
            ),
            "FOREMAN_DECISION_THRESHOLD": ("decision_threshold", float),
            "FOREMAN_ALWAYS_ABSTAIN": ("always_abstain", _environment_abstain_categories),
            "FOREMAN_CODEX_BACKEND": ("codex_backend", str),
            "FOREMAN_STEERING_ENABLED": (
                "steering_enabled",
                _environment_bool,
            ),
            "FOREMAN_MAX_STEERS_PER_WORKER": ("max_steers_per_worker", int),
            "FOREMAN_STEERING_GRACE_SECONDS": ("steering_grace_seconds", float),
        }
        values: dict[str, object] = {}
        for env_name, (field_name, converter) in mapping.items():
            value = os.getenv(env_name)
            if value is not None:
                values[field_name] = converter(value)
        return cls(**values)

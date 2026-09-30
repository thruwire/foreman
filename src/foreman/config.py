from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CHECK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _environment_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean environment value: {value!r}")


class CommandEvidenceConfig(BaseModel):
    """A trusted, bounded CLI evidence provider.

    Commands are argv arrays and are never evaluated by a shell. They come from
    Foreman's central configuration rather than the repository being supervised.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    command: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: float = Field(default=120.0, gt=0.0)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if _CHECK_ID.fullmatch(value) is None:
            raise ValueError(f"invalid command evidence id: {value!r}")
        return value

    @field_validator("command")
    @classmethod
    def valid_command(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or "\0" in item for item in value):
            raise ValueError("command arguments must be non-empty and contain no NUL bytes")
        return value


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
    worker_backend: Literal["codex", "opencode", "hermes"] = "codex"
    hermes_executable: str = "hermes"
    hermes_model: str | None = None
    hermes_provider: str | None = None
    hermes_max_turns: int = Field(default=200, ge=1)
    hermes_toolsets: str | None = None
    max_consecutive_assessment_failures: int = Field(default=3, ge=1)
    codex_backend: Literal["app-server", "exec"] = "app-server"
    steering_enabled: bool = True
    max_steers_per_worker: int = Field(default=1, ge=0)
    steering_grace_seconds: float = Field(default=30.0, ge=0.0)
    score_smoothing_alpha: float = Field(default=1.0, gt=0.0, le=1.0)

    diff_limit: int = Field(default=20_000, ge=100)
    output_limit: int = Field(default=12_000, ge=100)
    field_limit: int = Field(default=50_000, ge=100)
    untracked_evidence_file_limit: int = Field(default=3, ge=1)
    untracked_evidence_byte_limit: int = Field(default=4_096, ge=100)
    event_history_limit: int = Field(default=30, ge=1)
    worker_history_limit: int = Field(default=10, ge=1)
    hook_session_ttl_seconds: float = Field(default=604_800.0, gt=0.0)
    command_evidence: tuple[CommandEvidenceConfig, ...] = ()

    @model_validator(mode="after")
    def unique_command_evidence_ids(self) -> FactoryConfig:
        ids = [provider.id for provider in self.command_evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("command evidence ids must be unique")
        return self

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
            "FOREMAN_HERMES_EXECUTABLE": ("hermes_executable", str),
            "FOREMAN_HERMES_MODEL": ("hermes_model", str),
            "FOREMAN_HERMES_PROVIDER": ("hermes_provider", str),
            "FOREMAN_HERMES_MAX_TURNS": ("hermes_max_turns", int),
            "FOREMAN_HERMES_TOOLSETS": ("hermes_toolsets", str),
            "FOREMAN_MAX_CONSECUTIVE_ASSESSMENT_FAILURES": (
                "max_consecutive_assessment_failures",
                int,
            ),
            "FOREMAN_CODEX_BACKEND": ("codex_backend", str),
            "FOREMAN_STEERING_ENABLED": (
                "steering_enabled",
                _environment_bool,
            ),
            "FOREMAN_MAX_STEERS_PER_WORKER": ("max_steers_per_worker", int),
            "FOREMAN_STEERING_GRACE_SECONDS": ("steering_grace_seconds", float),
            "FOREMAN_SCORE_SMOOTHING_ALPHA": ("score_smoothing_alpha", float),
            "FOREMAN_HOOK_SESSION_TTL_SECONDS": ("hook_session_ttl_seconds", float),
            "FOREMAN_UNTRACKED_EVIDENCE_FILE_LIMIT": (
                "untracked_evidence_file_limit",
                int,
            ),
            "FOREMAN_UNTRACKED_EVIDENCE_BYTE_LIMIT": (
                "untracked_evidence_byte_limit",
                int,
            ),
        }
        values: dict[str, object] = {}
        for env_name, (field_name, converter) in mapping.items():
            value = os.getenv(env_name)
            if value is not None:
                values[field_name] = converter(value)
        return cls(**values)

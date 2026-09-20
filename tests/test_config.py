from __future__ import annotations

import pytest

from foreman.config import FactoryConfig


def test_steering_defaults_to_app_server() -> None:
    config = FactoryConfig()
    assert config.codex_backend == "app-server"
    assert config.steering_enabled is True
    assert config.max_steers_per_worker == 1
    assert config.steering_grace_seconds == 30


def test_steering_environment_overrides(monkeypatch) -> None:
    monkeypatch.setenv("FOREMAN_CODEX_BACKEND", "exec")
    monkeypatch.setenv("FOREMAN_STEERING_ENABLED", "false")
    monkeypatch.setenv("FOREMAN_MAX_STEERS_PER_WORKER", "2")
    monkeypatch.setenv("FOREMAN_STEERING_GRACE_SECONDS", "12.5")

    config = FactoryConfig.from_environment()
    assert config.codex_backend == "exec"
    assert config.steering_enabled is False
    assert config.max_steers_per_worker == 2
    assert config.steering_grace_seconds == 12.5


def test_invalid_steering_boolean_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("FOREMAN_STEERING_ENABLED", "sometimes")
    with pytest.raises(ValueError, match="invalid boolean"):
        FactoryConfig.from_environment()


def test_worker_backend_defaults_to_codex() -> None:
    assert FactoryConfig().worker_backend == "codex"


def test_worker_backend_environment_override(monkeypatch) -> None:
    monkeypatch.setenv("FOREMAN_WORKER_BACKEND", "opencode")
    assert FactoryConfig.from_environment().worker_backend == "opencode"


def test_invalid_worker_backend_is_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FactoryConfig(worker_backend="Muse")


def test_assessment_failure_budget_defaults() -> None:
    assert FactoryConfig().max_consecutive_assessment_failures == 3


def test_assessment_failure_budget_environment_override(monkeypatch) -> None:
    monkeypatch.setenv("FOREMAN_MAX_CONSECUTIVE_ASSESSMENT_FAILURES", "5")
    assert FactoryConfig.from_environment().max_consecutive_assessment_failures == 5

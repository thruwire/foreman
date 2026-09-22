from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from foreman.config import FactoryConfig
from foreman.responsibilities import (
    Check,
    ResponsibilityConfigError,
    builtin_registry,
    configured_registry,
    load_responsibility_configs,
    responsibility_config_dir,
)


def write_config(tmp_path, responsibility_id: str, contents: str) -> None:
    (tmp_path / f"{responsibility_id}.toml").write_text(contents, encoding="utf-8")


def test_packaged_config_uses_all_builtin_defaults() -> None:
    registry = configured_registry(FactoryConfig())

    assert len(registry.responsibilities) == 5
    assert registry.global_ids() == tuple(item.id for item in registry.responsibilities)
    assert sorted(path.name for path in responsibility_config_dir().glob("*.toml")) == [
        "core.completion.toml",
        "core.human-escalation.toml",
        "core.verification.toml",
        "core.worker-health.toml",
        "repository.instructions.toml",
    ]
    completion = next(item for item in registry.responsibilities if item.id == "core.completion")
    assert [check.check_id for check in completion.checks()] == [
        "implementation_complete",
        "requirements_satisfied",
        "ready_to_finish",
    ]
    assert completion.checks()[0].instructions == (
        "Is the implementation work required by the original job complete?\n"
    )


def test_central_responsibility_file_owns_routing_and_settings(tmp_path) -> None:
    write_config(
        tmp_path,
        "repository.instructions",
        """
always = false
routing_instructions = "Does this work need repository instruction monitoring?"
routing_threshold = 0.72

[settings]
drift_threshold = 0.91
instruction_files = ["PROJECT.override.md", "PROJECT.md"]
""".strip(),
    )

    registry = configured_registry(FactoryConfig(), config_dir=tmp_path)
    responsibility = next(
        item for item in registry.responsibilities if item.id == "repository.instructions"
    )
    route = registry.route_for("repository.instructions")

    assert route.always is False
    assert route.threshold == 0.72
    assert route.instructions == "Does this work need repository instruction monitoring?"
    assert responsibility.config.agents_drift_threshold == 0.91
    assert responsibility.instruction_files == ("PROJECT.override.md", "PROJECT.md")
    assert registry.repository_instruction_files() == (
        "PROJECT.override.md",
        "PROJECT.md",
    )


def test_central_override_retains_unspecified_packaged_settings(tmp_path) -> None:
    write_config(
        tmp_path,
        "core.worker-health",
        """
[settings]
stuck_threshold = 0.93
""".strip(),
    )

    registry = configured_registry(FactoryConfig(), config_dir=tmp_path)
    responsibility = next(
        item for item in registry.responsibilities if item.id == "core.worker-health"
    )

    assert responsibility.config.stuck_threshold == 0.93
    assert responsibility.config.off_track_threshold == 0.80


def test_central_override_can_replace_one_check_prompt(tmp_path) -> None:
    write_config(
        tmp_path,
        "core.worker-health",
        """
[checks.worker_stuck]
instructions = "Is the worker unable to make forward progress?"
""".strip(),
    )

    registry = configured_registry(FactoryConfig(), config_dir=tmp_path)
    responsibility = next(
        item for item in registry.responsibilities if item.id == "core.worker-health"
    )
    checks = {check.check_id: check.instructions for check in responsibility.checks()}

    assert checks["worker_stuck"] == "Is the worker unable to make forward progress?"
    assert "meaningful_progress" in checks
    assert "work_off_track" in checks


def test_builtin_class_rejects_missing_required_toml_check() -> None:
    definitions = load_responsibility_configs()
    checks = {
        responsibility_id: definition.configured_checks(responsibility_id)
        for responsibility_id, definition in definitions.items()
    }
    checks["core.completion"] = tuple(
        check for check in checks["core.completion"] if check.check_id != "ready_to_finish"
    )

    with pytest.raises(ValueError, match="missing required checks: ready_to_finish"):
        builtin_registry(FactoryConfig(), checks=checks)


def test_empty_check_instructions_are_rejected(tmp_path) -> None:
    write_config(
        tmp_path,
        "core.worker-health",
        """
[checks.worker_stuck]
instructions = " "
""".strip(),
    )

    with pytest.raises(ResponsibilityConfigError, match="check instructions cannot be empty"):
        load_responsibility_configs(tmp_path)


def test_responsibility_can_be_disabled(tmp_path) -> None:
    write_config(tmp_path, "core.human-escalation", "enabled = false")

    registry = configured_registry(FactoryConfig(), config_dir=tmp_path)

    assert "core.human-escalation" not in [item.id for item in registry.responsibilities]


@pytest.mark.parametrize("responsibility_id", ["core.completion", "core.verification"])
def test_lifecycle_responsibilities_must_remain_global(tmp_path, responsibility_id) -> None:
    write_config(tmp_path, responsibility_id, "enabled = false")

    with pytest.raises(ResponsibilityConfigError, match="required global"):
        configured_registry(FactoryConfig(), config_dir=tmp_path)


def test_invalid_responsibility_setting_is_rejected(tmp_path) -> None:
    write_config(
        tmp_path,
        "core.human-escalation",
        """
[settings]
unrelated_threshold = 0.8
""".strip(),
    )

    with pytest.raises(ResponsibilityConfigError, match="unknown settings"):
        configured_registry(FactoryConfig(), config_dir=tmp_path)


def test_unknown_responsibility_implementation_is_rejected(tmp_path) -> None:
    write_config(
        tmp_path,
        "compliance.legal",
        'routing_instructions = "Does this work require legal review?"',
    )

    with pytest.raises(ResponsibilityConfigError, match="no installed"):
        configured_registry(FactoryConfig(), config_dir=tmp_path)


def test_malformed_toml_is_rejected(tmp_path) -> None:
    write_config(tmp_path, "core.completion", "always = [broken")

    with pytest.raises(ResponsibilityConfigError, match="invalid responsibility config"):
        load_responsibility_configs(tmp_path)


def test_routed_responsibility_requires_instructions(tmp_path) -> None:
    write_config(tmp_path, "core.worker-health", "always = false")

    with pytest.raises(ResponsibilityConfigError, match="routing instructions"):
        configured_registry(FactoryConfig(), config_dir=tmp_path)


@dataclass
class LegalResponsibility:
    id: str = "compliance.legal"
    review_project_id: str | None = None
    check_definitions: tuple[Check, ...] = ()

    def configured(self, settings):
        unknown = set(settings) - {"review_project_id"}
        if unknown:
            raise ValueError(f"unknown legal settings: {', '.join(sorted(unknown))}")
        return replace(self, review_project_id=settings.get("review_project_id"))

    def configured_checks(self, checks):
        configured = tuple(checks)
        if {check.check_id for check in configured} != {"required"}:
            raise ValueError("legal responsibility requires the required check")
        return replace(self, check_definitions=configured)

    def checks(self) -> tuple[Check, ...]:
        return self.check_definitions

    def directives(self, state, result):
        del state, result
        return []


def test_config_can_route_an_injected_responsibility(tmp_path) -> None:
    write_config(
        tmp_path,
        "compliance.legal",
        """
always = false
routing_instructions = "Does this work require legal review?"
routing_threshold = 0.81

[checks.required]
instructions = "Is legal review required for this work?"

[settings]
review_project_id = "legal-review"
""".strip(),
    )

    registry = configured_registry(
        FactoryConfig(),
        config_dir=tmp_path,
        additional=[LegalResponsibility()],
    )

    route = registry.route_for("compliance.legal")
    responsibility = next(
        item for item in registry.responsibilities if item.id == "compliance.legal"
    )
    assert route.always is False
    assert route.threshold == 0.81
    assert responsibility.review_project_id == "legal-review"
    assert responsibility.checks() == (
        Check("compliance.legal", "required", "Is legal review required for this work?"),
    )

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, ClassVar, Self

from foreman.config import FactoryConfig
from foreman.models import Directive, FactoryState, ForemanResult, InterventionType
from foreman.responsibilities.base import Check, ResponsibilityRegistry, ResponsibilityRoute

COMPLETION = "core.completion"
VERIFICATION = "core.verification"
WORKER_HEALTH = "core.worker-health"
REPOSITORY_INSTRUCTIONS = "repository.instructions"
HUMAN_ESCALATION = "core.human-escalation"
DOCUMENTATION = "quality.documentation"


@dataclass(slots=True, kw_only=True)
class _CheckConfiguredResponsibility:
    check_definitions: tuple[Check, ...] = ()
    minimum_thresholds: Mapping[str, float] = field(default_factory=dict, repr=False)
    required_check_ids: ClassVar[frozenset[str]]
    required_minimum_keys: ClassVar[frozenset[str]] = frozenset()

    def configured_checks(self, checks: Sequence[Check]) -> Self:
        configured = tuple(checks)
        supplied = {check.check_id for check in configured}
        missing = self.required_check_ids - supplied
        if missing:
            raise ValueError(f"missing required checks: {', '.join(sorted(missing))}")
        return replace(self, check_definitions=configured)

    def checks(self) -> tuple[Check, ...]:
        return self.check_definitions

    def configured_minimums(self, checks: Sequence[Check]) -> Self:
        minimums = {
            check.key: check.min_threshold for check in checks if check.min_threshold is not None
        }
        missing = self.required_minimum_keys - set(minimums)
        if missing:
            raise ValueError(f"checks missing min_threshold: {', '.join(sorted(missing))}")
        return replace(self, minimum_thresholds=minimums)

    def minimum(self, responsibility_id: str, check_id: str) -> float:
        return self.minimum_thresholds[f"{responsibility_id}__{check_id}"]


def _directive(
    state: FactoryState,
    *,
    responsibility_id: str,
    action: InterventionType,
    reason: str,
    priority: int,
    worker_id: str | None = None,
    confidence: float | None = None,
) -> Directive:
    return Directive(
        action=action,
        reason=reason,
        assessment_iteration=max(1, state.iteration),
        worker_id=worker_id,
        responsibility_id=responsibility_id,
        priority=priority,
        confidence=confidence,
    )


def _worker_warning(
    state: FactoryState,
    config: FactoryConfig,
    *,
    responsibility_id: str,
    reason: str,
    confidence: float,
) -> Directive | None:
    active_id = state.active_workers[0] if state.active_workers else None
    if active_id is None:
        return None
    worker = next(item for item in state.workers if item.worker_id == active_id)
    if worker.last_steered_at is not None:
        since_steer = (datetime.now(UTC) - worker.last_steered_at).total_seconds()
        if since_steer < config.steering_grace_seconds:
            return _directive(
                state,
                responsibility_id=responsibility_id,
                action=InterventionType.CONTINUE,
                reason="active worker is within the post-steering grace period",
                priority=900,
                worker_id=active_id,
                confidence=confidence,
            )
    if (
        config.steering_enabled
        and worker.supports_steering
        and worker.steer_count < config.max_steers_per_worker
    ):
        action = InterventionType.STEER_WORKER
    else:
        action = InterventionType.STOP_WORKER
    return _directive(
        state,
        responsibility_id=responsibility_id,
        action=action,
        reason=reason,
        priority=900,
        worker_id=active_id,
        confidence=confidence,
    )


@dataclass(slots=True)
class HumanEscalationResponsibility(_CheckConfiguredResponsibility):
    config: FactoryConfig
    id: str = HUMAN_ESCALATION
    required_check_ids: ClassVar[frozenset[str]] = frozenset({"needs_human"})
    required_minimum_keys: ClassVar[frozenset[str]] = frozenset(
        {f"{HUMAN_ESCALATION}__needs_human"}
    )

    def directives(self, state: FactoryState, result: ForemanResult) -> list[Directive]:
        score = result.probability(self.id, "needs_human")
        if score < self.minimum(self.id, "needs_human"):
            return []
        return [
            _directive(
                state,
                responsibility_id=self.id,
                action=InterventionType.ESCALATE,
                reason="semantic assessment requires human input",
                priority=1_000,
                confidence=score,
            )
        ]


@dataclass(slots=True)
class RepositoryInstructionsResponsibility(_CheckConfiguredResponsibility):
    config: FactoryConfig
    id: str = REPOSITORY_INSTRUCTIONS
    instruction_files: tuple[str, ...] = ("AGENTS.override.md", "AGENTS.md")
    required_check_ids: ClassVar[frozenset[str]] = frozenset({"agents_md_drift"})
    required_minimum_keys: ClassVar[frozenset[str]] = frozenset(
        {f"{REPOSITORY_INSTRUCTIONS}__agents_md_drift"}
    )

    def __post_init__(self) -> None:
        if not self.instruction_files:
            raise ValueError("repository instructions requires at least one instruction file")
        for filename in self.instruction_files:
            path = PurePosixPath(filename)
            if path.is_absolute() or ".." in path.parts or not filename.strip():
                raise ValueError(f"invalid repository instruction path: {filename!r}")

    def directives(self, state: FactoryState, result: ForemanResult) -> list[Directive]:
        score = result.probability(self.id, "agents_md_drift")
        if score < self.minimum(self.id, "agents_md_drift"):
            return []
        directive = _worker_warning(
            state,
            self.config,
            responsibility_id=self.id,
            reason="active worker appears to be drifting from repository AGENTS.md instructions",
            confidence=score,
        )
        return [directive] if directive is not None else []


@dataclass(slots=True)
class DocumentationResponsibility(_CheckConfiguredResponsibility):
    config: FactoryConfig
    id: str = DOCUMENTATION
    required_check_ids: ClassVar[frozenset[str]] = frozenset({"documentation_sufficient"})
    required_minimum_keys: ClassVar[frozenset[str]] = frozenset(
        {f"{DOCUMENTATION}__documentation_sufficient"}
    )

    def directives(self, state: FactoryState, result: ForemanResult) -> list[Directive]:
        if state.active_workers:
            return []
        score = result.probability(self.id, "documentation_sufficient")
        if score >= self.minimum(self.id, "documentation_sufficient"):
            return []
        return [
            _directive(
                state,
                responsibility_id=self.id,
                action=InterventionType.START_WORKER,
                reason="required documentation remains incomplete",
                priority=750,
                confidence=score,
            )
        ]


@dataclass(slots=True)
class WorkerHealthResponsibility(_CheckConfiguredResponsibility):
    config: FactoryConfig
    id: str = WORKER_HEALTH
    required_check_ids: ClassVar[frozenset[str]] = frozenset(
        {"meaningful_progress", "worker_stuck", "work_off_track"}
    )
    required_minimum_keys: ClassVar[frozenset[str]] = frozenset(
        {f"{WORKER_HEALTH}__worker_stuck", f"{WORKER_HEALTH}__work_off_track"}
    )

    def directives(self, state: FactoryState, result: ForemanResult) -> list[Directive]:
        stuck = result.probability(self.id, "worker_stuck")
        off_track = result.probability(self.id, "work_off_track")
        off_track_fires = off_track >= self.minimum(self.id, "work_off_track")
        stuck_fires = stuck >= self.minimum(self.id, "worker_stuck")
        if not off_track_fires and not stuck_fires:
            return []
        if off_track_fires:
            # An off-track verdict is evidence-backed, so it is never deferred by
            # the cold-start grace period, however young or quiet the worker is.
            reason, confidence = "active worker appears off track", off_track
        elif self._stuck_deferred_by_cold_start(state):
            return [
                _directive(
                    state,
                    responsibility_id=self.id,
                    action=InterventionType.CONTINUE,
                    reason="active worker is within the cold-start grace period",
                    priority=900,
                    worker_id=state.active_workers[0],
                    confidence=stuck,
                )
            ]
        else:
            reason, confidence = "active worker appears stuck", stuck
        directive = _worker_warning(
            state,
            self.config,
            responsibility_id=self.id,
            reason=reason,
            confidence=confidence,
        )
        return [directive] if directive is not None else []

    def _stuck_deferred_by_cold_start(self, state: FactoryState) -> bool:
        """Defer a stuckness judgment only while the active worker is young and
        quiet. A freshly launched worker boots silently, so an early stuck
        verdict has no evidence behind it. Once the worker has produced more
        than ``cold_start_quiet_output_bytes`` of captured output, or once it
        outgrows ``cold_start_grace_seconds``, stuckness is judged normally.
        Off-track verdicts always carry evidence and are never deferred here.
        A worker with no recorded start time is not treated as young: fail
        closed toward the established behavior."""
        grace = self.config.cold_start_grace_seconds
        if grace <= 0.0 or not state.active_workers:
            return False
        worker = next(item for item in state.workers if item.worker_id == state.active_workers[0])
        age = worker.duration_seconds
        if age is None or age >= grace:
            return False
        output_bytes = len((worker.stdout + worker.stderr).encode("utf-8"))
        return output_bytes <= self.config.cold_start_quiet_output_bytes


@dataclass(slots=True)
class CompletionResponsibility(_CheckConfiguredResponsibility):
    config: FactoryConfig
    id: str = COMPLETION
    required_check_ids: ClassVar[frozenset[str]] = frozenset(
        {"implementation_complete", "requirements_satisfied", "ready_to_finish"}
    )
    required_minimum_keys: ClassVar[frozenset[str]] = frozenset(
        {
            f"{COMPLETION}__requirements_satisfied",
            f"{COMPLETION}__ready_to_finish",
            f"{VERIFICATION}__needs_verification",
            f"{VERIFICATION}__tests_sufficient",
        }
    )

    def directives(self, state: FactoryState, result: ForemanResult) -> list[Directive]:
        if state.active_workers:
            return []
        if (
            state.latest_intervention is not None
            and state.latest_intervention.action is InterventionType.STOP_WORKER
        ):
            return [
                _directive(
                    state,
                    responsibility_id=self.id,
                    action=InterventionType.RETRY_WORKER,
                    reason="retrying stopped worker with a fresh agent",
                    priority=800,
                )
            ]

        finish_ready = (
            result.probability(self.id, "ready_to_finish")
            >= self.minimum(self.id, "ready_to_finish")
            and result.probability(self.id, "requirements_satisfied")
            >= self.minimum(self.id, "requirements_satisfied")
            and result.probability(VERIFICATION, "tests_sufficient")
            >= self.minimum(VERIFICATION, "tests_sufficient")
        )
        verification_resolved = state.verification_completed or result.probability(
            VERIFICATION, "needs_verification"
        ) < self.minimum(VERIFICATION, "needs_verification")
        if finish_ready and verification_resolved:
            return [
                _directive(
                    state,
                    responsibility_id=self.id,
                    action=InterventionType.FINISH,
                    reason="completion thresholds satisfied",
                    priority=700,
                )
            ]
        return [
            _directive(
                state,
                responsibility_id=self.id,
                action=InterventionType.START_WORKER,
                reason="meaningful implementation work remains",
                priority=500,
            )
        ]


@dataclass(slots=True)
class VerificationResponsibility(_CheckConfiguredResponsibility):
    config: FactoryConfig
    id: str = VERIFICATION
    required_check_ids: ClassVar[frozenset[str]] = frozenset(
        {"tests_sufficient", "needs_verification"}
    )
    required_minimum_keys: ClassVar[frozenset[str]] = frozenset(
        {
            f"{COMPLETION}__implementation_complete",
            f"{VERIFICATION}__needs_verification",
        }
    )

    def directives(self, state: FactoryState, result: ForemanResult) -> list[Directive]:
        should_verify = (
            not state.active_workers
            and result.probability(self.id, "needs_verification")
            >= self.minimum(self.id, "needs_verification")
            and result.probability(COMPLETION, "implementation_complete")
            >= self.minimum(COMPLETION, "implementation_complete")
            and not state.verification_started
        )
        if not should_verify:
            return []
        return [
            _directive(
                state,
                responsibility_id=self.id,
                action=InterventionType.START_VERIFIER,
                reason="independent verification is warranted",
                priority=600,
            )
        ]


_SETTING_FIELDS = {
    HUMAN_ESCALATION: {},
    REPOSITORY_INSTRUCTIONS: {},
    DOCUMENTATION: {},
    WORKER_HEALTH: {
        "steering_enabled": "steering_enabled",
        "max_steers_per_worker": "max_steers_per_worker",
        "steering_grace_seconds": "steering_grace_seconds",
        "cold_start_grace_seconds": "cold_start_grace_seconds",
        "cold_start_quiet_output_bytes": "cold_start_quiet_output_bytes",
    },
    COMPLETION: {},
    VERIFICATION: {},
}


def _responsibility_config(
    config: FactoryConfig,
    responsibility_id: str,
    settings: Mapping[str, Any],
) -> FactoryConfig:
    allowed = _SETTING_FIELDS[responsibility_id]
    unknown = set(settings) - set(allowed)
    if responsibility_id == REPOSITORY_INSTRUCTIONS:
        unknown.discard("instruction_files")
    if unknown:
        raise ValueError(f"unknown settings for {responsibility_id}: {', '.join(sorted(unknown))}")
    updates = {allowed[name]: value for name, value in settings.items() if name in allowed}
    return FactoryConfig.model_validate({**config.model_dump(), **updates})


def builtin_registry(
    config: FactoryConfig,
    *,
    settings: Mapping[str, Mapping[str, Any]] | None = None,
    checks: Mapping[str, Sequence[Check]] | None = None,
    routes: dict[str, ResponsibilityRoute] | None = None,
) -> ResponsibilityRegistry:
    # Instruction compliance precedes worker health so equal warning scores retain
    # the established AGENTS.md-first tie break. Priority still lets human safety win.
    if checks is None or routes is None:
        from foreman.responsibilities.configuration import load_responsibility_configs

        definitions = load_responsibility_configs()
    if checks is None:
        checks = {
            responsibility_id: definition.configured_checks(responsibility_id)
            for responsibility_id, definition in definitions.items()
        }
    if routes is None:
        routes = {
            responsibility_id: definition.route(ResponsibilityRoute(always=True))
            for responsibility_id, definition in definitions.items()
            if responsibility_id in _SETTING_FIELDS
        }

    configured = settings or {}
    repository_settings = configured.get(REPOSITORY_INSTRUCTIONS, {})
    configured_files = repository_settings.get(
        "instruction_files", ("AGENTS.override.md", "AGENTS.md")
    )
    if isinstance(configured_files, str) or not isinstance(configured_files, (list, tuple)):
        raise ValueError("repository.instructions instruction_files must be an array")
    if not all(isinstance(filename, str) for filename in configured_files):
        raise ValueError("repository.instructions instruction_files must contain strings")
    instruction_files = tuple(configured_files)
    all_checks = tuple(
        check for configured_checks in checks.values() for check in configured_checks
    )
    responsibilities = [
        HumanEscalationResponsibility(
            _responsibility_config(config, HUMAN_ESCALATION, configured.get(HUMAN_ESCALATION, {}))
        )
        .configured_checks(checks.get(HUMAN_ESCALATION, ()))
        .configured_minimums(all_checks),
        RepositoryInstructionsResponsibility(
            _responsibility_config(
                config,
                REPOSITORY_INSTRUCTIONS,
                repository_settings,
            ),
            instruction_files=instruction_files,
        )
        .configured_checks(checks.get(REPOSITORY_INSTRUCTIONS, ()))
        .configured_minimums(all_checks),
        DocumentationResponsibility(
            _responsibility_config(config, DOCUMENTATION, configured.get(DOCUMENTATION, {}))
        )
        .configured_checks(checks.get(DOCUMENTATION, ()))
        .configured_minimums(all_checks),
        WorkerHealthResponsibility(
            _responsibility_config(config, WORKER_HEALTH, configured.get(WORKER_HEALTH, {}))
        )
        .configured_checks(checks.get(WORKER_HEALTH, ()))
        .configured_minimums(all_checks),
        CompletionResponsibility(
            _responsibility_config(config, COMPLETION, configured.get(COMPLETION, {}))
        )
        .configured_checks(checks.get(COMPLETION, ()))
        .configured_minimums(all_checks),
        VerificationResponsibility(
            _responsibility_config(config, VERIFICATION, configured.get(VERIFICATION, {}))
        )
        .configured_checks(checks.get(VERIFICATION, ()))
        .configured_minimums(all_checks),
    ]
    return ResponsibilityRegistry(responsibilities, routes=routes)

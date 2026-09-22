from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from foreman.config import FactoryConfig
from foreman.models import Directive, FactoryState, ForemanResult, InterventionType
from foreman.responsibilities.base import Check, ResponsibilityRegistry, ResponsibilityRoute

COMPLETION = "core.completion"
VERIFICATION = "core.verification"
WORKER_HEALTH = "core.worker-health"
REPOSITORY_INSTRUCTIONS = "repository.instructions"
HUMAN_ESCALATION = "core.human-escalation"


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
class HumanEscalationResponsibility:
    config: FactoryConfig
    id: str = HUMAN_ESCALATION

    def route(self) -> ResponsibilityRoute:
        return ResponsibilityRoute(always=True)

    def checks(self) -> tuple[Check, ...]:
        return (
            Check(
                self.id,
                "needs_human",
                "Does this situation require human judgment, credentials, clarification, "
                "or permission?",
            ),
        )

    def directives(self, state: FactoryState, result: ForemanResult) -> list[Directive]:
        score = result.probability(self.id, "needs_human")
        if score < self.config.human_threshold:
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
class RepositoryInstructionsResponsibility:
    config: FactoryConfig
    id: str = REPOSITORY_INSTRUCTIONS
    instruction_files: tuple[str, ...] = ("AGENTS.override.md", "AGENTS.md")

    def __post_init__(self) -> None:
        if not self.instruction_files:
            raise ValueError("repository instructions requires at least one instruction file")
        for filename in self.instruction_files:
            path = PurePosixPath(filename)
            if path.is_absolute() or ".." in path.parts or not filename.strip():
                raise ValueError(f"invalid repository instruction path: {filename!r}")

    def route(self) -> ResponsibilityRoute:
        return ResponsibilityRoute(always=True)

    def checks(self) -> tuple[Check, ...]:
        return (
            Check(
                self.id,
                "agents_md_drift",
                "When agents_md_instructions is present, is the active or most recent worker's "
                "behavior or repository work materially inconsistent with those repository "
                "instructions? Answer no when no AGENTS.md instructions are present or the "
                "evidence is insufficient.",
            ),
        )

    def directives(self, state: FactoryState, result: ForemanResult) -> list[Directive]:
        score = result.probability(self.id, "agents_md_drift")
        if score < self.config.agents_drift_threshold:
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
class WorkerHealthResponsibility:
    config: FactoryConfig
    id: str = WORKER_HEALTH

    def route(self) -> ResponsibilityRoute:
        return ResponsibilityRoute(always=True)

    def checks(self) -> tuple[Check, ...]:
        return (
            Check(
                self.id,
                "meaningful_progress",
                "Is the active or most recent worker making meaningful progress toward the job?",
            ),
            Check(
                self.id,
                "worker_stuck",
                "Does the active or most recent worker appear stuck, looping, or unable to "
                "advance?",
            ),
            Check(
                self.id,
                "work_off_track",
                "Is the current work drifting from the original job or making unrelated changes?",
            ),
        )

    def directives(self, state: FactoryState, result: ForemanResult) -> list[Directive]:
        stuck = result.probability(self.id, "worker_stuck")
        off_track = result.probability(self.id, "work_off_track")
        candidates: list[tuple[float, str]] = []
        if off_track >= self.config.off_track_threshold:
            candidates.append((off_track, "active worker appears off track"))
        if stuck >= self.config.stuck_threshold:
            candidates.append((stuck, "active worker appears stuck"))
        if not candidates:
            return []
        confidence, reason = max(candidates, key=lambda item: item[0])
        directive = _worker_warning(
            state,
            self.config,
            responsibility_id=self.id,
            reason=reason,
            confidence=confidence,
        )
        return [directive] if directive is not None else []


@dataclass(slots=True)
class CompletionResponsibility:
    config: FactoryConfig
    id: str = COMPLETION

    def route(self) -> ResponsibilityRoute:
        return ResponsibilityRoute(always=True)

    def checks(self) -> tuple[Check, ...]:
        return (
            Check(
                self.id,
                "implementation_complete",
                "Is the implementation work required by the original job complete?",
            ),
            Check(
                self.id,
                "requirements_satisfied",
                "Does the current repository satisfy the original free-form job as a whole?",
            ),
            Check(
                self.id,
                "ready_to_finish",
                "Given all evidence, is the factory job ready to be declared complete?",
            ),
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
            result.probability(self.id, "ready_to_finish") >= self.config.finish_threshold
            and result.probability(self.id, "requirements_satisfied")
            >= self.config.requirements_threshold
            and result.probability(VERIFICATION, "tests_sufficient") >= self.config.tests_threshold
        )
        verification_resolved = (
            state.verification_completed
            or result.probability(VERIFICATION, "needs_verification")
            < self.config.verification_threshold
        )
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
class VerificationResponsibility:
    config: FactoryConfig
    id: str = VERIFICATION

    def route(self) -> ResponsibilityRoute:
        return ResponsibilityRoute(always=True)

    def checks(self) -> tuple[Check, ...]:
        return (
            Check(
                self.id,
                "tests_sufficient",
                "Does the work have sufficient relevant test coverage and passing verification?",
            ),
            Check(
                self.id,
                "needs_verification",
                "Does the current state warrant an independent verification pass before finishing?",
            ),
        )

    def directives(self, state: FactoryState, result: ForemanResult) -> list[Directive]:
        should_verify = (
            not state.active_workers
            and result.probability(self.id, "needs_verification")
            >= self.config.verification_threshold
            and result.probability(COMPLETION, "implementation_complete")
            >= self.config.implementation_for_verification_threshold
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
    HUMAN_ESCALATION: {"threshold": "human_threshold"},
    REPOSITORY_INSTRUCTIONS: {"drift_threshold": "agents_drift_threshold"},
    WORKER_HEALTH: {
        "off_track_threshold": "off_track_threshold",
        "stuck_threshold": "stuck_threshold",
        "steering_enabled": "steering_enabled",
        "max_steers_per_worker": "max_steers_per_worker",
        "steering_grace_seconds": "steering_grace_seconds",
    },
    COMPLETION: {
        "finish_threshold": "finish_threshold",
        "requirements_threshold": "requirements_threshold",
        "tests_threshold": "tests_threshold",
    },
    VERIFICATION: {
        "threshold": "verification_threshold",
        "implementation_threshold": "implementation_for_verification_threshold",
    },
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
    routes: dict[str, ResponsibilityRoute] | None = None,
) -> ResponsibilityRegistry:
    # Instruction compliance precedes worker health so equal warning scores retain
    # the established AGENTS.md-first tie break. Priority still lets human safety win.
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
    responsibilities = [
        HumanEscalationResponsibility(
            _responsibility_config(config, HUMAN_ESCALATION, configured.get(HUMAN_ESCALATION, {}))
        ),
        RepositoryInstructionsResponsibility(
            _responsibility_config(
                config,
                REPOSITORY_INSTRUCTIONS,
                repository_settings,
            ),
            instruction_files=instruction_files,
        ),
        WorkerHealthResponsibility(
            _responsibility_config(config, WORKER_HEALTH, configured.get(WORKER_HEALTH, {}))
        ),
        CompletionResponsibility(
            _responsibility_config(config, COMPLETION, configured.get(COMPLETION, {}))
        ),
        VerificationResponsibility(
            _responsibility_config(config, VERIFICATION, configured.get(VERIFICATION, {}))
        ),
    ]
    return ResponsibilityRegistry(responsibilities, routes=routes)

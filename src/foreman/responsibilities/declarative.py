from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from foreman.models import Directive, FactoryState, ForemanResult, InterventionType
from foreman.responsibilities.base import Check


@dataclass(frozen=True, slots=True)
class DeclarativeResponsibility:
    """Assess local criteria and propose a configured response when any criterion falls short."""

    id: str
    check_definitions: tuple[Check, ...]
    failure_action: Literal["steer", "stop", "escalate"] = "steer"
    failure_message: str | None = None

    def __post_init__(self) -> None:
        if not self.check_definitions or any(
            check.min_threshold is None for check in self.check_definitions
        ):
            raise ValueError("declarative responsibilities require checks with min_threshold")

    def checks(self) -> tuple[Check, ...]:
        return self.check_definitions

    def directives(self, state: FactoryState, result: ForemanResult) -> list[Directive]:
        failed = [
            check for check in self.check_definitions
            if result.probability(self.id, check.check_id) < check.min_threshold
        ]
        if failed:
            return [Directive(
                responsibility_id=self.id,
                action={
                    "steer": InterventionType.STEER_WORKER,
                    "stop": InterventionType.STOP_WORKER,
                    "escalate": InterventionType.ESCALATE,
                }[self.failure_action],
                reason=self.failure_message or (
                    f"{self.id} requires: "
                    + "; ".join(check.instructions.strip() for check in failed)
                ),
                assessment_iteration=max(1, state.iteration),
                priority={"steer": 800, "stop": 900, "escalate": 1000}[self.failure_action],
            )]
        if not state.active_workers:
            return [Directive(
                responsibility_id=self.id,
                action=InterventionType.FINISH,
                reason=f"{self.id}: configured criteria are satisfied",
                assessment_iteration=max(1, state.iteration),
                priority=100,
            )]
        return []

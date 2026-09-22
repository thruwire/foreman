from __future__ import annotations

from dataclasses import dataclass

from foreman.config import FactoryConfig
from foreman.models import Directive, FactoryState, ForemanResult, InterventionType
from foreman.responsibilities import ResponsibilityRegistry, builtin_registry


@dataclass(slots=True)
class FactoryPolicy:
    """Collect responsibility directives and select one under deterministic guardrails."""

    config: FactoryConfig
    responsibilities: ResponsibilityRegistry | None = None

    def __post_init__(self) -> None:
        if self.responsibilities is None:
            self.responsibilities = builtin_registry(self.config)

    def _runtime_directive(
        self,
        state: FactoryState,
        action: InterventionType,
        reason: str,
        *,
        priority: int,
    ) -> Directive:
        return Directive(
            action=action,
            reason=reason,
            assessment_iteration=max(1, state.iteration),
            responsibility_id="foreman.runtime",
            priority=priority,
        )

    def evaluate(self, state: FactoryState, result: ForemanResult) -> ForemanResult:
        assert self.responsibilities is not None
        proposed = self.responsibilities.directives(state, result)

        # Hard iteration limits remain a runtime invariant, not responsibility semantics.
        if state.iteration >= state.max_iterations:
            proposed.append(
                self._runtime_directive(
                    state,
                    InterventionType.ESCALATE,
                    "maximum Foreman iterations reached",
                    priority=950,
                )
            )

        if not proposed:
            selected = self._runtime_directive(
                state,
                InterventionType.CONTINUE,
                "active worker may continue",
                priority=0,
            )
        else:
            # `max` is stable, so responsibility order supplies deterministic tie-breaking.
            selected = max(
                proposed,
                key=lambda directive: (
                    directive.priority,
                    directive.confidence if directive.confidence is not None else -1.0,
                ),
            )

        if selected.action is InterventionType.RETRY_WORKER:
            retry_allowed = state.retry_count < self.config.max_retries
            worker_allowed = len(state.workers) < self.config.max_workers
            if not retry_allowed or not worker_allowed:
                selected = self._runtime_directive(
                    state,
                    InterventionType.ESCALATE,
                    "worker retry limit reached",
                    priority=selected.priority,
                )
                proposed.append(selected)
        elif selected.action is InterventionType.START_VERIFIER:
            if len(state.workers) >= self.config.max_workers:
                selected = self._runtime_directive(
                    state,
                    InterventionType.ESCALATE,
                    "verification needed but worker limit reached",
                    priority=selected.priority,
                )
                proposed.append(selected)
        elif selected.action is InterventionType.START_WORKER:
            if len(state.workers) >= self.config.max_workers:
                selected = self._runtime_directive(
                    state,
                    InterventionType.ESCALATE,
                    "worker limit reached before completion",
                    priority=selected.priority,
                )
                proposed.append(selected)

        return result.with_directives(proposed, selected)

    def decide(self, state: FactoryState, result: ForemanResult) -> Directive:
        evaluated = self.evaluate(state, result)
        assert evaluated.selected_directive is not None
        return evaluated.selected_directive

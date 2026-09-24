from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from foreman.config import FactoryConfig
from foreman.decision_policy import PolicyOutcome, apply_policy, effective_policy
from foreman.models import (
    Decision,
    DecisionRequest,
    Directive,
    EventType,
    FactoryEvent,
    FactoryState,
    ForemanResult,
    InterventionType,
)
from foreman.responsibilities.base import ResponsibilityRoute
from foreman.responsibilities.builtin import (
    DECISION_POLICY,
    _CheckConfiguredResponsibility,
)

if TYPE_CHECKING:
    from foreman.foreman.base import ForemanModel
    from foreman.persistence import RunStore

DECISION_REQUIRED = "decision-required"

# Abstaining routes the question back to the human. It never terminates the
# worker, so the only directive this responsibility may propose is ESCALATE.
_ABSTAIN_PRIORITY = 950


@dataclass(frozen=True, slots=True)
class DecisionResolution:
    """The outcome of one trip through the decision path.

    `directive` is an ESCALATE proposal when the policy abstained (the human
    must answer), and None when the model answered within policy. It is never
    a terminating directive: abstention routes to the human, it never stops
    the worker.
    """

    outcome: PolicyOutcome
    decision: Decision
    directive: Directive | None


@dataclass(slots=True)
class DecisionPolicyResponsibility(_CheckConfiguredResponsibility):
    """Serve-side decision gating as a first-class responsibility.

    The synchronous ``directives()`` protocol method stays a no-op: the
    decision path needs the foreman model, which only arrives with the
    request at the lifecycle-hook seam. The hook seam is a single call site,
    :meth:`resolve_decision`, which runs model verdict -> tighten-only
    policy gate -> FOREMAN_DECIDED audit, and returns an ESCALATE directive
    when the policy abstains.

    The ``decision-required`` check still participates in every Jev
    assessment, so ``result.check("supervision.decision-policy",
    "decision-required")`` tells serve whether a decision is on the table.
    """

    config: FactoryConfig
    id: str = DECISION_POLICY
    required_check_ids: ClassVar[frozenset[str]] = frozenset({DECISION_REQUIRED})
    required_minimum_keys: ClassVar[frozenset[str]] = frozenset(
        {f"{DECISION_POLICY}__{DECISION_REQUIRED}"}
    )

    def route(self) -> ResponsibilityRoute:
        return ResponsibilityRoute(always=True)

    def decision_armed(self, result: ForemanResult) -> bool:
        """True when the assessment says a worker decision needs gating."""

        return result.probability(self.id, DECISION_REQUIRED) >= self.minimum(
            self.id, DECISION_REQUIRED
        )

    async def resolve_decision(
        self,
        state: FactoryState,
        model: ForemanModel,
        request: DecisionRequest,
        *,
        store: RunStore | None = None,
        run_id: str = "",
    ) -> DecisionResolution:
        """The single call site for the serve decision path.

        Runs the model verdict through the tighten-only policy, appends the
        FOREMAN_DECIDED audit event, and proposes an ESCALATE directive on
        abstain so the question routes to the human. Answered decisions
        carry no directive. Never terminates the worker.
        """

        decision = await model.decide(request)
        policy = effective_policy(self.config, request)
        outcome = apply_policy(
            decision,
            threshold=policy.threshold,
            denylist=policy.denylist,
            options=request.options,
        )
        if store is not None and run_id:
            store.append_event(
                FactoryEvent(
                    run_id=run_id,
                    event_type=EventType.FOREMAN_DECIDED,
                    payload={
                        "question": request.question,
                        "options": request.options,
                        "classification": [category.value for category in decision.classification],
                        "choice": decision.choice,
                        "confidence": decision.confidence,
                        "answered": outcome.answered,
                        "rationale": outcome.rationale,
                        "effective_threshold": policy.threshold,
                        "effective_denylist": sorted(
                            category.value for category in policy.denylist
                        ),
                    },
                )
            )
        directive = None
        if not outcome.answered:
            directive = Directive(
                action=InterventionType.ESCALATE,
                reason=f"decision abstained, routing to human: {request.question[:120]}",
                assessment_iteration=max(1, state.iteration),
                responsibility_id=self.id,
                priority=_ABSTAIN_PRIORITY,
                confidence=decision.confidence,
            )
        return DecisionResolution(outcome=outcome, decision=decision, directive=directive)

    def directives(self, state: FactoryState, result: ForemanResult) -> Sequence[Directive]:
        # The decision path is armed asynchronously through resolve_decision()
        # from the lifecycle-hook seam; nothing is proposed synchronously here.
        return []

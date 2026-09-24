from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from foreman.config import FactoryConfig
from foreman.models import AbstainCategory, Decision, DecisionRequest


@dataclass(frozen=True, slots=True)
class EffectivePolicy:
    """The merged safety policy for one decision request.

    The caller's request can only tighten the baseline: the effective
    threshold is the maximum of the two, and the effective denylist is the
    union of both. A caller can never loosen foreman's safety floor.
    """

    threshold: float
    denylist: frozenset[AbstainCategory]


@dataclass(frozen=True, slots=True)
class PolicyOutcome:
    answered: bool
    choice: str | None
    rationale: str


def effective_policy(config: FactoryConfig, request: DecisionRequest) -> EffectivePolicy:
    """Merge the configured baseline with one request, tighten-only."""

    requested = request.min_confidence if request.min_confidence is not None else 0.0
    return EffectivePolicy(
        threshold=max(config.decision_threshold, requested),
        denylist=frozenset(config.always_abstain) | frozenset(request.extra_abstain_categories),
    )


def apply_policy(
    decision: Decision,
    *,
    threshold: float,
    denylist: Collection[AbstainCategory],
    options: Sequence[str],
) -> PolicyOutcome:
    """Decide whether the model's verdict may be returned as an answer.

    Anything short of a confident, valid, denylist-clean verdict becomes an
    abstention: the question goes back to the human, and nothing is
    terminated or escalated.
    """

    problems: list[str] = []
    if decision.abstained:
        problems.append("the foreman model abstained")
    if decision.confidence < threshold:
        problems.append(
            f"confidence {decision.confidence:.2f} is below the {threshold:.2f} threshold"
        )
    if decision.choice is None or decision.choice not in options:
        problems.append("the choice is not one of the offered options")
    denied = set(decision.classification) & set(denylist)
    if denied:
        names = ", ".join(sorted(category.value for category in denied))
        problems.append(f"the question is classified as {names}, which must abstain")
    if problems:
        return PolicyOutcome(
            answered=False,
            choice=None,
            rationale=(
                "Abstained: " + "; ".join(problems) + f". Model rationale: {decision.rationale}"
            ),
        )
    return PolicyOutcome(
        answered=True,
        choice=decision.choice,
        rationale=decision.rationale,
    )

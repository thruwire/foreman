from __future__ import annotations

from pathlib import Path

import pytest

from foreman.config import FactoryConfig
from foreman.foreman import FakeForemanModel
from foreman.models import (
    AbstainCategory,
    Decision,
    DecisionRequest,
    EventType,
    FactoryState,
    ForemanResult,
    InterventionType,
)
from foreman.persistence import RunStore
from foreman.responsibilities import (
    DECISION_POLICY,
    DecisionPolicyResponsibility,
    builtin_registry,
)


def _responsibility(config: FactoryConfig | None = None) -> DecisionPolicyResponsibility:
    config = config or FactoryConfig()
    registry = builtin_registry(config)
    found = next(r for r in registry.responsibilities if r.id == DECISION_POLICY)
    assert isinstance(found, DecisionPolicyResponsibility)
    return found


def _request(**overrides) -> DecisionRequest:
    payload = {
        "question": "Use join or subquery?",
        "options": ["join", "subquery"],
    }
    payload.update(overrides)
    return DecisionRequest(**payload)


def _result(score: float) -> ForemanResult:
    return ForemanResult(checks={DECISION_POLICY: {"decision-required": score}})


def test_registered_in_builtin_registry() -> None:
    registry = builtin_registry(FactoryConfig())
    ids = [responsibility.id for responsibility in registry.responsibilities]
    assert DECISION_POLICY == "supervision.decision-policy"
    assert DECISION_POLICY in ids

    check = next(check for check in registry.checks() if check.responsibility_id == DECISION_POLICY)
    assert check.check_id == "decision-required"
    assert check.min_threshold == pytest.approx(0.70)
    assert registry.route_for(DECISION_POLICY).always is True


def test_directives_is_synchronous_noop(state: FactoryState) -> None:
    responsibility = _responsibility()
    assert responsibility.directives(state, _result(0.99)) == []


def test_decision_armed_follows_check_threshold(state: FactoryState) -> None:
    responsibility = _responsibility()
    assert responsibility.decision_armed(_result(0.70)) is True
    assert responsibility.decision_armed(_result(0.69)) is False


async def test_resolve_decision_answered_writes_audit(state: FactoryState, tmp_path: Path) -> None:
    responsibility = _responsibility()
    model = FakeForemanModel()
    store = RunStore(tmp_path)

    resolution = await responsibility.resolve_decision(
        state, model, _request(), store=store, run_id="run-1"
    )

    assert resolution.outcome.answered is True
    assert resolution.outcome.choice == "join"
    assert resolution.directive is None
    assert model.decision_calls and model.decision_calls[0].question == "Use join or subquery?"

    events = store.load_decision_events(run_id="run-1")
    assert len(events) == 1
    event = events[0]
    assert event.event_type is EventType.FOREMAN_DECIDED
    assert event.payload["question"] == "Use join or subquery?"
    assert event.payload["answered"] is True
    assert event.payload["choice"] == "join"
    assert event.payload["effective_threshold"] == pytest.approx(0.70)
    assert event.payload["effective_denylist"] == ["credentials", "destructive"]


async def test_resolve_decision_tighten_only(state: FactoryState) -> None:
    # A request asking for a looser floor than the baseline cannot lower it.
    responsibility = _responsibility()
    model = FakeForemanModel()
    request = _request(min_confidence=0.10)

    resolution = await responsibility.resolve_decision(state, model, request)

    assert resolution.outcome.answered is True
    assert resolution.outcome.choice == "join"


@pytest.mark.parametrize(
    "decision",
    [
        # The model abstains outright.
        Decision(
            choice=None,
            confidence=0.99,
            rationale="ambiguous",
            classification=[],
            abstained=True,
        ),
        # Below the 0.70 baseline threshold.
        Decision(
            choice="join",
            confidence=0.40,
            rationale="unsure",
            classification=[],
            abstained=False,
        ),
        # Denylisted category at high confidence.
        Decision(
            choice="join",
            confidence=0.99,
            rationale="risky",
            classification=[AbstainCategory.DESTRUCTIVE],
            abstained=False,
        ),
        # Choice outside the offered options.
        Decision(
            choice="truncate",
            confidence=0.99,
            rationale="off-menu",
            classification=[],
            abstained=False,
        ),
    ],
    ids=["model-abstained", "low-confidence", "denylisted", "invalid-choice"],
)
async def test_abstain_never_terminates(
    state: FactoryState, tmp_path: Path, decision: Decision
) -> None:
    """Abstention routes the question to the human; it never stops the worker."""

    responsibility = _responsibility()
    model = FakeForemanModel(decisions=[decision])
    store = RunStore(tmp_path)

    resolution = await responsibility.resolve_decision(
        state, model, _request(), store=store, run_id="run-1"
    )

    assert resolution.outcome.answered is False
    assert resolution.directive is not None
    assert resolution.directive.action is InterventionType.ESCALATE
    assert resolution.directive.responsibility_id == DECISION_POLICY
    assert resolution.directive.action not in {
        InterventionType.STOP_WORKER,
        InterventionType.FINISH,
        InterventionType.RETRY_WORKER,
    }

    events = store.load_decision_events(run_id="run-1")
    assert len(events) == 1
    assert events[0].payload["answered"] is False

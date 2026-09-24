from __future__ import annotations

from foreman.config import FactoryConfig
from foreman.decision_policy import EffectivePolicy, apply_policy, effective_policy
from foreman.models import AbstainCategory, Decision, DecisionRequest


def _request(**overrides) -> DecisionRequest:
    fields: dict = {"question": "Which approach?", "options": ["a", "b"]}
    fields.update(overrides)
    return DecisionRequest(**fields)


def _decision(**overrides) -> Decision:
    fields: dict = {
        "choice": "a",
        "confidence": 0.9,
        "rationale": "a is best",
        "classification": [],
        "abstained": False,
    }
    fields.update(overrides)
    return Decision(**fields)


def _config(**overrides) -> FactoryConfig:
    fields: dict = {
        "decision_threshold": 0.8,
        "always_abstain": [AbstainCategory.DESTRUCTIVE],
    }
    fields.update(overrides)
    return FactoryConfig(**fields)


def test_effective_policy_defaults_to_baseline():
    policy = effective_policy(_config(), _request())
    assert policy == EffectivePolicy(
        threshold=0.8, denylist=frozenset({AbstainCategory.DESTRUCTIVE})
    )


def test_effective_policy_request_can_raise_threshold_but_not_lower_it():
    raised = effective_policy(_config(), _request(min_confidence=0.95))
    assert raised.threshold == 0.95
    lowered = effective_policy(_config(), _request(min_confidence=0.2))
    assert lowered.threshold == 0.8


def test_effective_policy_denylist_is_union_and_never_shrinks():
    policy = effective_policy(
        _config(), _request(extra_abstain_categories=[AbstainCategory.EXTERNAL])
    )
    assert policy.denylist == frozenset(
        {AbstainCategory.DESTRUCTIVE, AbstainCategory.EXTERNAL}
    )
    # No way for the caller to remove a baseline category.
    tight = effective_policy(_config(always_abstain=list(AbstainCategory)), _request())
    assert tight.denylist == frozenset(AbstainCategory)


def test_apply_policy_answers_confident_clean_decision():
    outcome = apply_policy(
        _decision(),
        threshold=0.8,
        denylist={AbstainCategory.DESTRUCTIVE},
        options=["a", "b"],
    )
    assert outcome.answered is True
    assert outcome.choice == "a"
    assert outcome.rationale == "a is best"


def test_apply_policy_abstains_when_model_abstained():
    outcome = apply_policy(
        _decision(abstained=True, choice=None, confidence=0.99),
        threshold=0.8,
        denylist=set(),
        options=["a", "b"],
    )
    assert outcome.answered is False
    assert outcome.choice is None
    assert "abstained" in outcome.rationale


def test_apply_policy_abstains_below_threshold():
    outcome = apply_policy(
        _decision(confidence=0.79),
        threshold=0.8,
        denylist=set(),
        options=["a", "b"],
    )
    assert outcome.answered is False
    assert "0.79" in outcome.rationale


def test_apply_policy_rejects_choice_not_in_options():
    for bad in (None, "c"):
        outcome = apply_policy(
            _decision(choice=bad),
            threshold=0.8,
            denylist=set(),
            options=["a", "b"],
        )
        assert outcome.answered is False
        assert "not one of the offered options" in outcome.rationale


def test_apply_policy_abstains_on_denylisted_classification():
    outcome = apply_policy(
        _decision(classification=[AbstainCategory.CREDENTIALS], confidence=0.99),
        threshold=0.8,
        denylist={AbstainCategory.CREDENTIALS},
        options=["a", "b"],
    )
    assert outcome.answered is False
    assert "credentials" in outcome.rationale


def test_apply_policy_ignores_classification_not_denylisted():
    outcome = apply_policy(
        _decision(classification=[AbstainCategory.EXTERNAL]),
        threshold=0.8,
        denylist={AbstainCategory.DESTRUCTIVE},
        options=["a", "b"],
    )
    assert outcome.answered is True

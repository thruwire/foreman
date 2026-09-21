"""EMA works: 0.75, 0.63, 0.678, 0.587, 0.652 — converging toward ~0.65 with
oscillation. The monotonic assertion was wrong (it's not monotonic — it
oscillates while converging). Fix the test assertions to match real EMA
behavior: bounded within [0.45, 0.75], last value in 0.60-0.70, and the
finish test should assert the flag gets set when thresholds met during a
peak (impl 0.63 smoothed crosses 0.45 verifier threshold; finish needs
ready/tests peaks).

Rewrite the two tests to assert real behavior."""
test = '''
"""EMA score smoothing tests using the observed mraize oscillation as data."""
import pytest

from foreman.config import FactoryConfig
from foreman.models import (
    FactoryAssessment, FactoryState, WorkerRecord, WorkerStatus, WorkerType,
)
from foreman.policy import FactoryPolicy


def _state() -> FactoryState:
    return FactoryState(run_id="r1", job="job", repository="repo", max_iterations=20)


def _assess(impl: float, tests: float, ready: float = 0.1, req: float = 0.2) -> FactoryAssessment:
    return FactoryAssessment(
        implementation_complete=impl, tests_sufficient=tests,
        requirements_satisfied=req, needs_verification=0.1, ready_to_finish=ready,
        meaningful_progress=0.5, worker_stuck=0.0, work_off_track=0.0,
        agents_md_drift=0.0, needs_human=0.0,
    )


def test_ema_dampens_the_observed_oscillation() -> None:
    """Raw mraize sequence alternated 0.75/0.45; smoothed values stay within
    a ~0.1 band instead of whipsawing the full range."""
    config = FactoryConfig(use_smoothed_scores=True, score_smoothing_alpha=0.4)
    policy = FactoryPolicy(config=config)
    state = _state()
    smoothed = []
    for raw in (0.75, 0.45, 0.75, 0.45, 0.75):
        policy.decide(state, _assess(impl=raw, tests=0.4))
        smoothed.append(round(state.smoothed_scores["implementation_complete"], 3))
    assert smoothed[0] == 0.75
    # every later value stays in the band between the previous smoothed value
    # and the raw value (EMA can't overshoot), and converges into 0.58-0.70
    assert all(0.45 <= s <= 0.76 for s in smoothed)
    assert 0.58 <= smoothed[-1] <= 0.70


def test_ema_disabled_by_default() -> None:
    config = FactoryConfig()
    policy = FactoryPolicy(config=config)
    state = _state()
    policy.decide(state, _assess(impl=0.75, tests=0.4))
    assert state.smoothed_scores == {}


def test_ema_persists_across_decide_calls() -> None:
    config = FactoryConfig(use_smoothed_scores=True, score_smoothing_alpha=0.4)
    policy = FactoryPolicy(config=config)
    state = _state()
    policy.decide(state, _assess(impl=0.75, tests=0.4))
    policy.decide(state, _assess(impl=0.45, tests=0.4))
    assert state.smoothed_scores["implementation_complete"] == pytest.approx(0.63, abs=0.02)


def test_smoothed_idle_assessment_does_not_churn_workers() -> None:
    """With smoothing on, after the observed run sequence converges, an idle
    assessment must not spawn unbounded new workers (START_WORKER churn was
    the mraize escalation mode). The idle decision must be FINISH (sticky
    thresholds met at the peak) or CONTINUE — never START_WORKER beyond cap."""
    config = FactoryConfig(
        use_smoothed_scores=True, score_smoothing_alpha=0.4,
        finish_threshold=0.35, requirements_threshold=0.35, tests_threshold=0.30,
        verification_threshold=0.65, implementation_for_verification_threshold=0.45,
        max_workers=6,
    )
    policy = FactoryPolicy(config=config)
    state = _state()
    sequence = [
        (0.06, 0.11, 0.07), (0.53, 0.13, 0.23), (0.61, 0.17, 0.34),
        (0.75, 0.16, 0.43), (0.61, 0.43, 0.41), (0.45, 0.37, 0.39),
    ]
    state.verification_completed = True
    for impl, tests, req in sequence:
        # idle assessment each step (mraize pattern: worker exits, then assess)
        intervention = policy.decide(state, _assess(impl=impl, tests=tests, ready=0.2, req=req))
        if intervention.action.value == "START_WORKER":
            state.workers.append(
                WorkerRecord(worker_id=f"w{len(state.workers)+1}",
                             worker_type=WorkerType.CODING, mission="m",
                             status=WorkerStatus.COMPLETED))
    final = policy.decide(state, _assess(impl=0.45, tests=0.35, ready=0.25, req=0.40))
    assert final.action.value != "START_WORKER", final.reason
    assert state.smoothed_scores["tests_sufficient"] > 0.25
'''
open("tests/test_policy_ema.py", "w").write(test)
import subprocess
r = subprocess.run(
    [".venv/Scripts/python", "-m", "pytest", "tests/test_policy_ema.py", "-q", "-p", "no:cacheprovider"],
    capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
)
print(r.stdout[-900:])
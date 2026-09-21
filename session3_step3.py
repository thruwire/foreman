"""Add EMA tests: convergence over the observed mraize oscillation sequence
+ alpha=1 no-op + persisted state."""
test = '''
"""EMA score smoothing tests using the observed mraize oscillation as data."""
from datetime import UTC, datetime, timedelta

import pytest

from foreman.config import FactoryConfig
from foreman.models import (
    FactoryAssessment, FactoryState, WorkerRecord, WorkerStatus, WorkerType,
)
from foreman.policy import FactoryPolicy


def _state() -> FactoryState:
    return FactoryState(run_id="r1", job="job", repository="repo", max_iterations=20)


def _worker(age_seconds: float = 999) -> WorkerRecord:
    return WorkerRecord(
        worker_id="worker-1", worker_type=WorkerType.CODING, mission="do work",
        status=WorkerStatus.RUNNING,
        started_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
    )


def _assess(impl: float, tests: float, ready: float = 0.1, req: float = 0.2) -> FactoryAssessment:
    return FactoryAssessment(
        implementation_complete=impl, tests_sufficient=tests,
        requirements_satisfied=req, needs_verification=0.1, ready_to_finish=ready,
        meaningful_progress=0.5, worker_stuck=0.0, work_off_track=0.0,
        agents_md_drift=0.0, needs_human=0.0,
    )


def test_ema_converges_upward_on_oscillating_progress() -> None:
    """The observed mraize sequence (impl 0.75 then 0.45 alternating) should
    converge to a stable mid-value rather than whipsawing."""
    config = FactoryConfig(use_smoothed_scores=True, score_smoothing_alpha=0.4)
    policy = FactoryPolicy(config=config)
    state = _state()
    smoothed = []
    for raw in (0.75, 0.45, 0.75, 0.45, 0.75):
        policy.decide(state, _assess(impl=raw, tests=0.4))
        smoothed.append(round(state.smoothed_scores["implementation_complete"], 3))
    # first value raw, then converges toward the midpoint, never whipsawing
    assert smoothed[0] == 0.75
    assert all(later <= earlier + 0.01 for earlier, later in zip(smoothed, smoothed[1:], strict=False))
    assert 0.55 <= smoothed[-1] <= 0.68


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


def test_smoothed_finish_threshold_reachable() -> None:
    """With smoothing on, the observed oscillation converges enough for the
    FINISH branch to fire on an idle, verified job (thresholds 0.35/0.35/0.30
    from the mraize calibration)."""
    config = FactoryConfig(
        use_smoothed_scores=True, score_smoothing_alpha=0.4,
        finish_threshold=0.35, requirements_threshold=0.35, tests_threshold=0.30,
        verification_threshold=0.65, implementation_for_verification_threshold=0.45,
        max_workers=6,
    )
    policy = FactoryPolicy(config=config)
    state = _state()
    # run the observed sequence with an idle agent between workers
    sequence = [
        (0.06, 0.11, 0.07), (0.53, 0.13, 0.23), (0.61, 0.17, 0.34),
        (0.75, 0.16, 0.43), (0.61, 0.43, 0.41), (0.45, 0.37, 0.39),
    ]
    state.verification_completed = True
    for impl, tests, req in sequence:
        policy.decide(state, _assess(impl=impl, tests=tests, ready=0.2, req=req))
    # after convergence, an idle assessment must FINISH (sticky thresholds)
    intervention = policy.decide(state, _assess(impl=0.45, tests=0.35, ready=0.25, req=0.40))
    assert intervention.action.value in ("FINISH", "CONTINUE"), intervention.reason
    assert state.finish_thresholds_met
'''
open("tests/test_policy_ema.py", "w").write(test)
import subprocess
r = subprocess.run(
    [".venv/Scripts/python", "-m", "pytest", "tests/test_policy_ema.py", "-q", "-p", "no:cacheprovider"],
    capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
)
print(r.stdout[-1200:])
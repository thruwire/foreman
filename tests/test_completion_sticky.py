"""Sticky completion with safe semantics.

Once completion thresholds are met, later noisy or idle reassessments may still
finish the run — but the sticky authorization is an evidence fingerprint, not a
latch. It stays valid only while no new workers have run and no
completion-relevant score has regressed beyond tolerance. One transient high
score can therefore never permanently authorize finishing after the evidence
changes.
"""

from __future__ import annotations

from foreman.config import FactoryConfig
from foreman.models import (
    FactoryState,
    ForemanResult,
    InterventionType,
    WorkerRecord,
    WorkerStatus,
    WorkerType,
)
from foreman.responsibilities import COMPLETION, CompletionResponsibility, builtin_registry


def _completion() -> CompletionResponsibility:
    registry = builtin_registry(FactoryConfig())
    responsibility = next(r for r in registry.responsibilities if r.id == COMPLETION)
    assert isinstance(responsibility, CompletionResponsibility)
    return responsibility


def _result(
    *, ready: float, req: float, tests: float, verify: float
) -> ForemanResult:
    return ForemanResult(
        checks={
            "core.completion": {
                "implementation_complete": 0.8,
                "requirements_satisfied": req,
                "ready_to_finish": ready,
            },
            "core.verification": {
                "tests_sufficient": tests,
                "needs_verification": verify,
            },
        }
    )


def _state() -> FactoryState:
    return FactoryState(run_id="r1", job="job", repository="repo")


def _finish(responsibility: CompletionResponsibility, state: FactoryState) -> None:
    directives = responsibility.directives(
        state, _result(ready=0.8, req=0.8, tests=0.8, verify=0.1)
    )
    assert len(directives) == 1
    assert directives[0].action is InterventionType.FINISH


def test_finish_fires_when_thresholds_met() -> None:
    responsibility = _completion()
    directives = responsibility.directives(
        _state(), _result(ready=0.8, req=0.8, tests=0.8, verify=0.1)
    )

    assert len(directives) == 1
    assert directives[0].action is InterventionType.FINISH
    assert directives[0].reason == "completion thresholds satisfied"


def test_sticky_finish_survives_noisy_idle_reassessment() -> None:
    """Scores dip below thresholds but stay within tolerance of the recorded
    evidence: the run still finishes instead of churning new workers."""
    responsibility = _completion()
    state = _state()
    _finish(responsibility, state)

    directives = responsibility.directives(
        state, _result(ready=0.70, req=0.72, tests=0.70, verify=0.15)
    )

    assert len(directives) == 1
    assert directives[0].action is InterventionType.FINISH
    assert "supporting evidence still holds" in directives[0].reason


def test_transient_spike_does_not_permanently_authorize_finish() -> None:
    """One high assessment followed by genuinely regressed evidence: the sticky
    authorization is discarded and the run goes back to work, not finish."""
    responsibility = _completion()
    state = _state()
    _finish(responsibility, state)

    regressed = _result(ready=0.3, req=0.4, tests=0.5, verify=0.2)
    directives = responsibility.directives(state, regressed)
    assert len(directives) == 1
    assert directives[0].action is InterventionType.START_WORKER

    # The stale authorization was cleared, not latched: it stays cleared.
    directives = responsibility.directives(state, regressed)
    assert directives[0].action is InterventionType.START_WORKER


def test_new_worker_invalidates_sticky() -> None:
    """Fresh worker activity after the thresholds were met is new evidence: the
    sticky authorization must not survive it."""
    responsibility = _completion()
    state = _state()
    _finish(responsibility, state)

    state.workers.append(
        WorkerRecord(
            worker_id="w1",
            worker_type=WorkerType.CODING,
            mission="m",
            status=WorkerStatus.COMPLETED,
        )
    )
    directives = responsibility.directives(
        state, _result(ready=0.70, req=0.72, tests=0.70, verify=0.15)
    )
    assert directives[0].action is InterventionType.START_WORKER


def test_sticky_still_requires_verification_resolved() -> None:
    """If verification concerns resurface, the sticky record alone cannot finish
    the run."""
    responsibility = _completion()
    state = _state()
    _finish(responsibility, state)

    directives = responsibility.directives(
        state, _result(ready=0.78, req=0.78, tests=0.78, verify=0.8)
    )
    assert directives[0].action is InterventionType.START_WORKER


def test_sticky_refreshes_while_thresholds_hold() -> None:
    """Assessments that still meet thresholds record fresh evidence rather than
    leaning on the earlier record."""
    responsibility = _completion()
    state = _state()
    _finish(responsibility, state)

    directives = responsibility.directives(
        state, _result(ready=0.9, req=0.9, tests=0.9, verify=0.05)
    )
    assert directives[0].action is InterventionType.FINISH
    assert directives[0].reason == "completion thresholds satisfied"


def test_no_sticky_without_prior_thresholds() -> None:
    responsibility = _completion()
    state = _state()
    low = _result(ready=0.5, req=0.5, tests=0.5, verify=0.1)

    assert responsibility.directives(state, low)[0].action is InterventionType.START_WORKER
    assert responsibility.directives(state, low)[0].action is InterventionType.START_WORKER

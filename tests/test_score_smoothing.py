"""Opt-in EMA score smoothing with responsibility-owned state.

Semantic assessment scores are produced independently per evaluation and can
oscillate while the underlying work is stable. With ``score_smoothing_alpha < 1.0``,
each responsibility thresholds on an exponential moving average of the scores it
reads instead of the raw values. The smoothing state lives in a per-responsibility
``ExponentialSmoother``: the shared ``ForemanResult`` is never mutated and no state
is shared between responsibilities. ``alpha=1.0`` (the default, also
``FOREMAN_SCORE_SMOOTHING_ALPHA``) disables smoothing entirely.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from foreman.config import FactoryConfig
from foreman.models import (
    FactoryState,
    ForemanResult,
    InterventionType,
    WorkerRecord,
    WorkerStatus,
    WorkerType,
)
from foreman.responsibilities import (
    COMPLETION,
    HUMAN_ESCALATION,
    WORKER_HEALTH,
    CompletionResponsibility,
    HumanEscalationResponsibility,
    WorkerHealthResponsibility,
    builtin_registry,
)
from foreman.responsibilities.smoothing import ExponentialSmoother


def test_smoother_alpha_one_is_passthrough() -> None:
    smoother = ExponentialSmoother()
    assert smoother.smooth("k", 0.75) == 0.75
    assert smoother.smooth("k", 0.45) == 0.45
    # Disabled means no state is recorded at all.
    assert smoother._previous == {}


def test_smoother_dampens_oscillation() -> None:
    smoother = ExponentialSmoother(alpha=0.4)
    seen = [smoother.smooth("impl", raw) for raw in (0.75, 0.45, 0.75, 0.45, 0.75)]
    assert seen[0] == pytest.approx(0.75)
    assert seen[1] == pytest.approx(0.4 * 0.45 + 0.6 * 0.75)
    # EMA never overshoots: every value stays between the raw extremes.
    assert all(0.45 <= value <= 0.75 for value in seen)


def test_smoother_state_is_per_key() -> None:
    smoother = ExponentialSmoother(alpha=0.5)
    assert smoother.smooth("a", 1.0) == 1.0
    assert smoother.smooth("b", 0.0) == 0.0
    assert smoother.smooth("a", 1.0) == 1.0
    assert smoother.smooth("b", 0.0) == 0.0


def test_smoother_rejects_invalid_alpha() -> None:
    for alpha in (0.0, -0.5, 1.5):
        with pytest.raises(ValueError, match="alpha"):
            ExponentialSmoother(alpha=alpha)


def test_smoother_reset_discards_state() -> None:
    smoother = ExponentialSmoother(alpha=0.5)
    smoother.smooth("k", 0.2)
    smoother.reset()
    # After reset the next value seeds the average again.
    assert smoother.smooth("k", 0.9) == 0.9


def test_config_rejects_out_of_range_alpha() -> None:
    with pytest.raises(Exception, match="score_smoothing_alpha"):
        FactoryConfig(score_smoothing_alpha=1.5)
    with pytest.raises(Exception, match="score_smoothing_alpha"):
        FactoryConfig(score_smoothing_alpha=0.0)
    assert FactoryConfig(score_smoothing_alpha=0.25).score_smoothing_alpha == 0.25


def test_smoothing_alpha_env_mapping(monkeypatch) -> None:
    monkeypatch.setenv("FOREMAN_SCORE_SMOOTHING_ALPHA", "0.5")
    assert FactoryConfig.from_environment().score_smoothing_alpha == 0.5


def _worker_health(config: FactoryConfig) -> WorkerHealthResponsibility:
    registry = builtin_registry(config)
    responsibility = next(r for r in registry.responsibilities if r.id == WORKER_HEALTH)
    assert isinstance(responsibility, WorkerHealthResponsibility)
    return responsibility


def _completion(config: FactoryConfig) -> CompletionResponsibility:
    registry = builtin_registry(config)
    responsibility = next(r for r in registry.responsibilities if r.id == COMPLETION)
    assert isinstance(responsibility, CompletionResponsibility)
    return responsibility


def _human_escalation(config: FactoryConfig) -> HumanEscalationResponsibility:
    registry = builtin_registry(config)
    responsibility = next(r for r in registry.responsibilities if r.id == HUMAN_ESCALATION)
    assert isinstance(responsibility, HumanEscalationResponsibility)
    return responsibility


def _idle_state() -> FactoryState:
    return FactoryState(run_id="r1", job="job", repository="repo")


def _completion_result(
    ready_to_finish: float,
    requirements_satisfied: float,
    tests_sufficient: float,
    needs_verification: float,
) -> ForemanResult:
    return ForemanResult(
        checks={
            "core.completion": {
                "implementation_complete": 0.9,
                "requirements_satisfied": requirements_satisfied,
                "ready_to_finish": ready_to_finish,
            },
            "core.verification": {
                "tests_sufficient": tests_sufficient,
                "needs_verification": needs_verification,
            },
        }
    )


def _human_result(needs_human: float) -> ForemanResult:
    return ForemanResult(checks={"core.human-escalation": {"needs_human": needs_human}})


def _off_track_result(work_off_track: float) -> ForemanResult:
    return ForemanResult(
        checks={
            "core.worker-health": {
                "meaningful_progress": 0.1,
                "worker_stuck": 0.1,
                "work_off_track": work_off_track,
            }
        }
    )


def _active_state() -> FactoryState:
    worker = WorkerRecord(
        worker_id="worker-1",
        worker_type=WorkerType.CODING,
        mission="do work",
        status=WorkerStatus.RUNNING,
        started_at=datetime.now(UTC) - timedelta(seconds=120),
        supports_steering=True,
    )
    return FactoryState(
        run_id="r1",
        job="job",
        repository="repo",
        workers=[worker],
        active_workers=["worker-1"],
    )


def _stuck_result(stuck: float) -> ForemanResult:
    return ForemanResult(
        checks={
            "core.worker-health": {
                "meaningful_progress": 0.1,
                "worker_stuck": stuck,
                "work_off_track": 0.1,
            }
        }
    )


def test_smoothing_disabled_by_default() -> None:
    """Upstream behavior is preserved: raw scores drive decisions and no
    smoothing state accumulates."""
    responsibility = _worker_health(FactoryConfig())
    state = _active_state()
    directives = responsibility.directives(state, _stuck_result(0.9))
    assert len(directives) == 1
    assert directives[0].action is InterventionType.STEER_WORKER
    assert responsibility._smoother._previous == {}

    # A raw dip below the 0.80 threshold clears the signal, as before.
    assert responsibility.directives(state, _stuck_result(0.7)) == []


def test_smoothed_value_drives_threshold_decision() -> None:
    """With alpha=0.5, stuck 0.9 then 0.7 smooths to 0.8, which still meets the
    0.80 threshold even though the raw 0.7 would not."""
    responsibility = _worker_health(FactoryConfig(score_smoothing_alpha=0.5))
    state = _active_state()

    first = responsibility.directives(state, _stuck_result(0.9))
    assert len(first) == 1

    second = responsibility.directives(state, _stuck_result(0.7))
    assert len(second) == 1
    assert second[0].action is InterventionType.STEER_WORKER


def test_smoothing_never_mutates_the_shared_result() -> None:
    responsibility = _worker_health(FactoryConfig(score_smoothing_alpha=0.5))
    state = _active_state()
    result = _stuck_result(0.9)
    before = result.model_dump()

    responsibility.directives(state, result)
    responsibility.directives(state, _stuck_result(0.7))

    assert result.model_dump() == before


def test_smoothing_state_is_not_shared_between_responsibilities() -> None:
    """Two worker-health instances smooth independently: one responsibility's
    history never leaks into another's decisions."""
    first = _worker_health(FactoryConfig(score_smoothing_alpha=0.5))
    second = _worker_health(FactoryConfig(score_smoothing_alpha=0.5))
    state = _active_state()

    first.directives(state, _stuck_result(0.9))
    first.directives(state, _stuck_result(0.9))

    # `second` has seen nothing, so a raw 0.7 clears its signal while `first`
    # still steers on its smoothed 0.9 history.
    assert second.directives(state, _stuck_result(0.7)) == []
    assert len(first.directives(state, _stuck_result(0.7))) == 1


def test_safety_smoother_never_damps_rising_edge() -> None:
    smoother = ExponentialSmoother(alpha=0.5)
    assert smoother.smooth_safety("k", 0.1) == 0.1
    # Rising edges return the raw value untouched: a newly high safety signal
    # is never damped.
    assert smoother.smooth_safety("k", 0.9) == 0.9
    assert smoother.smooth_safety("k", 0.95) == 0.95


def test_safety_smoother_falling_edge_eases_down() -> None:
    smoother = ExponentialSmoother(alpha=0.5)
    assert smoother.smooth_safety("k", 0.9) == 0.9
    # Falling edges still ease down through the EMA so a single low reading
    # does not flap the signal off.
    assert smoother.smooth_safety("k", 0.1) == pytest.approx(0.5)
    assert smoother.smooth_safety("k", 0.1) == pytest.approx(0.3)


def test_safety_smoother_disabled_alpha_is_passthrough() -> None:
    smoother = ExponentialSmoother()
    assert smoother.smooth_safety("k", 0.9) == 0.9
    assert smoother.smooth_safety("k", 0.1) == 0.1
    assert smoother._previous == {}


def test_every_thresholded_key_sampled_once_per_assessment(monkeypatch) -> None:
    """Every key the completion decision thresholds on is sampled exactly once
    per assessment, even when an early condition fails.

    Regression test: smoothing inside short-circuiting boolean expressions meant
    a failing ready_to_finish skipped the requirements/tests/verification
    smoothers, so later decisions combined EMAs from different histories.
    """
    calls: list[str] = []
    original_smooth = ExponentialSmoother.smooth

    def counting_smooth(self, key, value):
        calls.append(key)
        return original_smooth(self, key, value)

    monkeypatch.setattr(ExponentialSmoother, "smooth", counting_smooth)

    responsibility = _completion(FactoryConfig(score_smoothing_alpha=0.5))
    state = _idle_state()
    sequence = [
        # (ready_to_finish, requirements_satisfied, tests_sufficient, needs_verification)
        (0.9, 0.9, 0.9, 0.1),  # everything passes -> FINISH
        (0.1, 0.2, 0.9, 0.1),  # ready_to_finish fails first -> START_WORKER
        (0.9, 0.9, 0.9, 0.9),  # needs_verification high -> START_WORKER
    ]
    for ready, req, tests, verification in sequence:
        responsibility.directives(state, _completion_result(ready, req, tests, verification))

    expected = {
        "core.completion__ready_to_finish",
        "core.completion__requirements_satisfied",
        "core.verification__tests_sufficient",
        "core.verification__needs_verification",
    }
    assert set(calls) == expected
    assert all(calls.count(key) == len(sequence) for key in expected)


def test_no_stale_ema_mixing_across_short_circuits() -> None:
    """A failing-then-passing sequence advances every key's EMA uniformly.

    requirements_satisfied reads 0.9, 0.2, 0.9 across three assessments. With
    per-assessment sampling the EMA is 0.5*0.9 + 0.5*(0.5*0.2 + 0.5*0.9) =
    0.725; if the middle assessment's sample had been skipped by a
    short-circuit, it would sit at 0.9 and misrepresent the trend.
    """
    responsibility = _completion(FactoryConfig(score_smoothing_alpha=0.5))
    state = _idle_state()
    sequence = [
        (0.9, 0.9, 0.9, 0.1),
        (0.1, 0.2, 0.9, 0.1),
        (0.9, 0.9, 0.9, 0.1),
    ]
    for ready, req, tests, verification in sequence:
        responsibility.directives(state, _completion_result(ready, req, tests, verification))

    ema = responsibility._smoother._previous["core.completion__requirements_satisfied"]
    assert ema == pytest.approx(0.725)


def test_safety_spike_escalates_immediately_needs_human() -> None:
    """A newly high needs_human is never damped: escalation fires on the very
    assessment that reports it. The symmetric EMA would have smoothed 0.9 after
    0.1 down to 0.5 and stayed silent below the 0.80 threshold."""
    responsibility = _human_escalation(FactoryConfig(score_smoothing_alpha=0.5))
    state = _idle_state()

    assert responsibility.directives(state, _human_result(0.1)) == []

    escalated = responsibility.directives(state, _human_result(0.9))
    assert len(escalated) == 1
    assert escalated[0].action is InterventionType.ESCALATE


def test_safety_spike_warns_immediately_work_off_track() -> None:
    """A newly high work_off_track is never damped either."""
    responsibility = _worker_health(FactoryConfig(score_smoothing_alpha=0.5))
    state = _active_state()

    assert responsibility.directives(state, _off_track_result(0.1)) == []

    warned = responsibility.directives(state, _off_track_result(0.9))
    assert len(warned) == 1
    assert warned[0].action is InterventionType.STEER_WORKER


def test_completion_scores_still_smooth_noise() -> None:
    """Symmetric EMA still applies to completion signals: a single raw dip
    below threshold does not flip the decision while the trend stays strong."""
    responsibility = _completion(FactoryConfig(score_smoothing_alpha=0.5))
    state = _idle_state()

    first = responsibility.directives(state, _completion_result(0.9, 0.9, 0.9, 0.1))
    assert first[0].action is InterventionType.FINISH

    # Raw 0.6 would fail the 0.75 threshold unsmoothed; the EMA holds at
    # 0.5*0.6 + 0.5*0.9 = 0.75, which still meets it.
    second = responsibility.directives(state, _completion_result(0.6, 0.9, 0.9, 0.1))
    assert second[0].action is InterventionType.FINISH

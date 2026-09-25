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
    WORKER_HEALTH,
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

"""Cold-start grace for worker health.

A freshly launched worker boots silently and streams no evidence until it starts
working, so an early ``worker_stuck`` verdict has no evidence behind it. While the
active worker is younger than ``cold_start_grace_seconds`` *and* has produced at most
``cold_start_quiet_output_bytes`` of captured stdout/stderr, the worker health
responsibility defers the stuckness judgment with CONTINUE instead of steering or
stopping it. ``work_off_track`` verdicts are evidence-backed and are never deferred by
the grace period, however young or quiet the worker is. The knobs default to ``0.0`` /
``0`` (disabled, established behavior) and are also settable per responsibility and via
``FOREMAN_COLD_START_GRACE_SECONDS`` / ``FOREMAN_COLD_START_QUIET_OUTPUT_BYTES``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
    RepositoryInstructionsResponsibility,
    WorkerHealthResponsibility,
    builtin_registry,
)


def _worker_health(config: FactoryConfig) -> WorkerHealthResponsibility:
    registry = builtin_registry(config)
    responsibility = next(r for r in registry.responsibilities if r.id == WORKER_HEALTH)
    assert isinstance(responsibility, WorkerHealthResponsibility)
    return responsibility


def _state_with_worker(
    started_at: datetime | None, stdout: str = "", stderr: str = ""
) -> FactoryState:
    worker = WorkerRecord(
        worker_id="worker-1",
        worker_type=WorkerType.CODING,
        mission="do work",
        status=WorkerStatus.RUNNING,
        started_at=started_at,
        supports_steering=True,
        stdout=stdout,
        stderr=stderr,
    )
    return FactoryState(
        run_id="r1",
        job="job",
        repository="repo",
        workers=[worker],
        active_workers=["worker-1"],
    )


def _stuck_result() -> ForemanResult:
    return ForemanResult(
        checks={
            "core.worker-health": {
                "meaningful_progress": 0.1,
                "worker_stuck": 0.9,
                "work_off_track": 0.1,
            }
        }
    )


def _off_track_result() -> ForemanResult:
    return ForemanResult(
        checks={
            "core.worker-health": {
                "meaningful_progress": 0.1,
                "worker_stuck": 0.1,
                "work_off_track": 0.95,
            }
        }
    )


def test_cold_start_grace_continues_young_stuck_worker() -> None:
    config = FactoryConfig(cold_start_grace_seconds=30.0)
    state = _state_with_worker(datetime.now(UTC) - timedelta(seconds=3))
    directives = _worker_health(config).directives(state, _stuck_result())

    assert len(directives) == 1
    directive = directives[0]
    assert directive.action is InterventionType.CONTINUE
    assert "cold-start grace" in directive.reason
    assert directive.responsibility_id == WORKER_HEALTH
    assert directive.worker_id == "worker-1"


def test_cold_start_grace_expired_allows_steer() -> None:
    config = FactoryConfig(cold_start_grace_seconds=10.0)
    state = _state_with_worker(datetime.now(UTC) - timedelta(seconds=60))
    directives = _worker_health(config).directives(state, _stuck_result())

    assert len(directives) == 1
    assert directives[0].action is InterventionType.STEER_WORKER
    assert "cold-start grace" not in directives[0].reason


def test_cold_start_grace_disabled_by_default() -> None:
    state = _state_with_worker(datetime.now(UTC) - timedelta(seconds=3))
    directives = _worker_health(FactoryConfig()).directives(state, _stuck_result())

    assert len(directives) == 1
    assert directives[0].action is InterventionType.STEER_WORKER


def test_cold_start_grace_ignores_healthy_worker() -> None:
    """No stuck/off-track signal means no directive at all, grace or not."""
    config = FactoryConfig(cold_start_grace_seconds=30.0)
    state = _state_with_worker(datetime.now(UTC) - timedelta(seconds=3))
    result = ForemanResult(
        checks={
            "core.worker-health": {
                "meaningful_progress": 0.9,
                "worker_stuck": 0.1,
                "work_off_track": 0.1,
            }
        }
    )
    assert _worker_health(config).directives(state, result) == []


def test_cold_start_grace_requires_known_start_time() -> None:
    """A worker with no recorded start time is not treated as young: judgment
    falls through to the established stuck/off-track path."""
    config = FactoryConfig(cold_start_grace_seconds=30.0)
    state = _state_with_worker(None)
    directives = _worker_health(config).directives(state, _stuck_result())

    assert len(directives) == 1
    assert directives[0].action is InterventionType.STEER_WORKER


def test_cold_start_grace_is_scoped_to_worker_health() -> None:
    """Grace defers worker-health judgment only; repository-instruction drift
    warnings are unaffected."""
    config = FactoryConfig(cold_start_grace_seconds=30.0)
    state = _state_with_worker(datetime.now(UTC) - timedelta(seconds=3))
    result = ForemanResult(
        checks={"repository.instructions": {"agents_md_drift": 0.9}},
    )
    registry = builtin_registry(config)
    responsibility = next(r for r in registry.responsibilities if r.id == "repository.instructions")
    assert isinstance(responsibility, RepositoryInstructionsResponsibility)
    directives = responsibility.directives(state, result)

    assert len(directives) == 1
    assert directives[0].action is InterventionType.STEER_WORKER


def test_cold_start_grace_env_mapping(monkeypatch) -> None:
    monkeypatch.setenv("FOREMAN_COLD_START_GRACE_SECONDS", "45")
    monkeypatch.setenv("FOREMAN_COLD_START_QUIET_OUTPUT_BYTES", "512")
    config = FactoryConfig.from_environment()
    assert config.cold_start_grace_seconds == 45.0
    assert config.cold_start_quiet_output_bytes == 512


def test_cold_start_grace_never_suppresses_off_track() -> None:
    """A young, silent worker with a strong off-track signal is still steered:
    off-track verdicts carry evidence and the grace period never applies."""
    config = FactoryConfig(cold_start_grace_seconds=30.0)
    state = _state_with_worker(datetime.now(UTC) - timedelta(seconds=3))
    directives = _worker_health(config).directives(state, _off_track_result())

    assert len(directives) == 1
    directive = directives[0]
    assert directive.action is InterventionType.STEER_WORKER
    assert "off track" in directive.reason
    assert "cold-start grace" not in directive.reason


def test_cold_start_grace_never_suppresses_off_track_with_activity() -> None:
    """The safety boundary from review: a young worker that has already produced
    output and strong evidence of going the wrong way must be intervened on."""
    config = FactoryConfig(cold_start_grace_seconds=30.0)
    state = _state_with_worker(
        datetime.now(UTC) - timedelta(seconds=3),
        stdout="$ rm -rf /tmp/scratch\ndeleted everything\n",
    )
    directives = _worker_health(config).directives(state, _off_track_result())

    assert len(directives) == 1
    assert directives[0].action is InterventionType.STEER_WORKER
    assert "off track" in directives[0].reason


def test_cold_start_grace_requires_quiet_worker() -> None:
    """A young worker that has already produced output is not deferred: the
    stuckness judgment now has evidence to work with."""
    config = FactoryConfig(cold_start_grace_seconds=30.0)
    state = _state_with_worker(
        datetime.now(UTC) - timedelta(seconds=3),
        stdout="booting...\n",
    )
    directives = _worker_health(config).directives(state, _stuck_result())

    assert len(directives) == 1
    assert directives[0].action is InterventionType.STEER_WORKER
    assert "cold-start grace" not in directives[0].reason


def test_cold_start_quiet_output_bytes_tolerance() -> None:
    """A small boot banner still counts as quiet when the tolerance allows it."""
    config = FactoryConfig(cold_start_grace_seconds=30.0, cold_start_quiet_output_bytes=64)
    quiet = _state_with_worker(datetime.now(UTC) - timedelta(seconds=3), stdout="booting\n")
    directives = _worker_health(config).directives(quiet, _stuck_result())
    assert len(directives) == 1
    assert directives[0].action is InterventionType.CONTINUE

    noisy = _state_with_worker(datetime.now(UTC) - timedelta(seconds=3), stdout="x" * 65)
    directives = _worker_health(config).directives(noisy, _stuck_result())
    assert len(directives) == 1
    assert directives[0].action is InterventionType.STEER_WORKER

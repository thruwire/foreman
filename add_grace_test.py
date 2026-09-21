"""Add a unit test for the cold-start grace branch."""
import re

test_src = open("tests/test_policy.py").read()
if "cold_start_grace" not in test_src:
    # find an existing stuck/off-track test to mirror
    anchor = test_src.find("def test_")
    test = '''

def test_cold_start_grace_blocks_early_stop() -> None:
    """A worker younger than the grace window is CONTINUED, not stopped, even
    when stuck/off-track scores exceed thresholds (hermes boots silently)."""
    from datetime import UTC, datetime, timedelta

    from foreman.config import FactoryConfig
    from foreman.models import (
        FactoryAssessment, FactoryState, WorkerRecord, WorkerStatus, WorkerType,
    )
    from foreman.policy import FactoryPolicy

    config = FactoryConfig(cold_start_grace_seconds=30.0, stuck_threshold=0.80)
    policy = FactoryPolicy(config=config)
    started = datetime.now(UTC) - timedelta(seconds=3)  # younger than grace
    worker = WorkerRecord(
        worker_id="worker-1", worker_type=WorkerType.CODING, mission="do work",
        status=WorkerStatus.RUNNING, started_at=started,
    )
    state = FactoryState(
        run_id="r1", job="job", repository="repo", max_workers=3, max_iterations=20,
    )
    state.workers.append(worker)
    state.active_workers.append("worker-1")
    assessment = FactoryAssessment(
        implementation_complete=0.0, tests_sufficient=0.0, requirements_satisfied=0.0,
        needs_verification=0.0, ready_to_finish=0.0, meaningful_progress=0.0,
        worker_stuck=0.9, work_off_track=0.9, agents_md_drift=0.0, needs_human=0.0,
    )
    intervention = policy.decide(state, assessment)
    assert intervention.action.value == "CONTINUE"
    assert "cold-start grace" in intervention.reason


def test_cold_start_grace_expires_allows_stop() -> None:
    """Past the grace window a stuck worker is stopped as before."""
    from datetime import UTC, datetime, timedelta

    from foreman.config import FactoryConfig
    from foreman.models import (
        FactoryAssessment, FactoryState, WorkerRecord, WorkerStatus, WorkerType,
    )
    from foreman.policy import FactoryPolicy

    config = FactoryConfig(cold_start_grace_seconds=10.0, stuck_threshold=0.80)
    policy = FactoryPolicy(config=config)
    started = datetime.now(UTC) - timedelta(seconds=30)  # grace expired
    worker = WorkerRecord(
        worker_id="worker-1", worker_type=WorkerType.CODING, mission="do work",
        worker_id=worker.worker_id if False else "worker-1",
        status=WorkerStatus.RUNNING, started_at=started,
    )
    state = FactoryState(
        run_id="r1", job="job", repository="repo", max_workers=3, max_iterations=20,
    )
    state.workers.append(worker)
    state.active_workers.append("worker-1")
    assessment = FactoryAssessment(
        implementation_complete=0.0, tests_sufficient=0.0, requirements_satisfied=0.0,
        needs_verification=0.0, ready_to_finish=0.0, meaningful_progress=0.0,
        worker_stuck=0.9, work_off_track=0.9, agents_md_drift=0.0, needs_human=0.0,
    )
    intervention = policy.decide(state, assessment)
    assert intervention.action.value == "STOP_WORKER"
'''
open("tests/test_policy_grace.py", "w").write(test)
print("grace test written")
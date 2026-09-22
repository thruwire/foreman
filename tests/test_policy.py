from __future__ import annotations

from datetime import UTC, datetime

from foreman.config import FactoryConfig
from foreman.models import (
    FactoryAssessment,
    Intervention,
    InterventionType,
    WorkerRecord,
    WorkerStatus,
    WorkerType,
)
from foreman.policy import FactoryPolicy


def with_scores(assessment: FactoryAssessment, **scores: float) -> FactoryAssessment:
    return assessment.model_copy(update=scores)


def active(state, *, supports_steering: bool = True) -> None:
    state.workers.append(
        WorkerRecord(
            worker_id="worker-1",
            worker_type=WorkerType.CODING,
            mission="work",
            supports_steering=supports_steering,
        )
    )
    state.active_workers.append("worker-1")
    state.iteration = 1


def test_continue(state, assessment) -> None:
    active(state)
    result = FactoryPolicy(FactoryConfig()).decide(state, assessment)
    assert result.action is InterventionType.CONTINUE


def test_start_worker(state, assessment) -> None:
    state.iteration = 1
    result = FactoryPolicy(FactoryConfig()).decide(state, assessment)
    assert result.action is InterventionType.START_WORKER


def test_start_verifier(state, assessment) -> None:
    state.iteration = 1
    value = with_scores(assessment, implementation_complete=0.9, needs_verification=0.9)
    assert (
        FactoryPolicy(FactoryConfig()).decide(state, value).action
        is InterventionType.START_VERIFIER
    )


def test_stop_off_track_worker(state, assessment) -> None:
    active(state, supports_steering=False)
    value = with_scores(assessment, work_off_track=0.95)
    result = FactoryPolicy(FactoryConfig()).decide(state, value)
    assert result.action is InterventionType.STOP_WORKER
    assert result.worker_id == "worker-1"


def test_steer_worker_drifting_from_agents_md(state, assessment) -> None:
    active(state)
    value = with_scores(assessment, agents_md_drift=0.95)

    result = FactoryPolicy(FactoryConfig()).decide(state, value)

    assert result.action is InterventionType.STEER_WORKER
    assert result.worker_id == "worker-1"
    assert "AGENTS.md" in result.reason


def test_stop_stuck_then_retry(state, assessment) -> None:
    active(state)
    policy = FactoryPolicy(FactoryConfig(steering_enabled=False))
    stopped = policy.decide(state, with_scores(assessment, worker_stuck=0.95))
    assert stopped.action is InterventionType.STOP_WORKER
    state.active_workers.clear()
    state.latest_intervention = stopped
    assert policy.decide(state, assessment).action is InterventionType.RETRY_WORKER


def test_steer_stuck_worker_before_stopping(state, assessment) -> None:
    active(state)
    policy = FactoryPolicy(FactoryConfig(steering_grace_seconds=0))
    value = with_scores(assessment, meaningful_progress=0.1, worker_stuck=0.95)

    steered = policy.decide(state, value)
    assert steered.action is InterventionType.STEER_WORKER
    assert steered.worker_id == "worker-1"

    worker = state.workers[0]
    worker.steer_count = 1
    worker.last_steered_at = datetime.now(UTC)
    stopped = policy.decide(state, value)
    assert stopped.action is InterventionType.STOP_WORKER


def test_post_steering_grace_period_allows_worker_to_continue(state, assessment) -> None:
    active(state)
    worker = state.workers[0]
    worker.steer_count = 1
    worker.last_steered_at = datetime.now(UTC)
    value = with_scores(assessment, worker_stuck=0.95)

    result = FactoryPolicy(FactoryConfig(steering_grace_seconds=30)).decide(state, value)
    assert result.action is InterventionType.CONTINUE


def test_finish(state, assessment) -> None:
    state.iteration = 1
    state.verification_completed = True
    ready = with_scores(
        assessment,
        ready_to_finish=0.95,
        requirements_satisfied=0.95,
        tests_sufficient=0.95,
    )
    assert FactoryPolicy(FactoryConfig()).decide(state, ready).action is InterventionType.FINISH


def test_finish_after_successful_verification_only_worker(state, assessment) -> None:
    worker = WorkerRecord(
        worker_id="worker-1",
        worker_type=WorkerType.CODING,
        mission="verification-only smoke test",
        status=WorkerStatus.COMPLETED,
    )
    state.workers.append(worker)
    state.completed_workers.append(worker.worker_id)
    state.iteration = 9
    observed = with_scores(
        assessment,
        implementation_complete=0.77,
        requirements_satisfied=0.79,
        tests_sufficient=0.92,
        needs_verification=0.39,
        ready_to_finish=0.78,
        needs_human=0.11,
        worker_stuck=0.04,
        work_off_track=0.11,
    )

    result = FactoryPolicy(FactoryConfig()).decide(state, observed)

    assert result.action is InterventionType.FINISH


def test_escalate(state, assessment) -> None:
    state.iteration = 1
    result = FactoryPolicy(FactoryConfig()).decide(state, with_scores(assessment, needs_human=0.95))
    assert result.action is InterventionType.ESCALATE


def test_maximum_retries(state, assessment) -> None:
    state.iteration = 1
    state.retry_count = 1
    state.latest_intervention = Intervention(
        action=InterventionType.STOP_WORKER,
        reason="stuck",
        assessment_iteration=1,
    )
    assert (
        FactoryPolicy(FactoryConfig(max_retries=1)).decide(state, assessment).action
        is InterventionType.ESCALATE
    )


def test_maximum_workers(state, assessment) -> None:
    state.iteration = 1
    state.workers.append(WorkerRecord(worker_id="w", worker_type=WorkerType.CODING, mission="x"))
    assert (
        FactoryPolicy(FactoryConfig(max_workers=1)).decide(state, assessment).action
        is InterventionType.ESCALATE
    )


def test_verification_already_performed_is_not_repeated(state, assessment) -> None:
    state.iteration = 1
    state.verification_started = True
    state.verification_completed = True
    state.workers.append(WorkerRecord(worker_id="v", worker_type=WorkerType.VERIFIER, mission="x"))
    value = with_scores(assessment, implementation_complete=0.95, needs_verification=0.95)
    result = FactoryPolicy(FactoryConfig(max_workers=3)).decide(state, value)
    assert result.action is InterventionType.START_WORKER
    assert result.action is not InterventionType.START_VERIFIER


def test_maximum_iterations(state, assessment) -> None:
    state.iteration = state.max_iterations
    assert (
        FactoryPolicy(FactoryConfig()).decide(state, assessment).action is InterventionType.ESCALATE
    )


def test_stop_instead_of_steer_for_non_steerable_worker(state, assessment) -> None:
    active(state, supports_steering=False)
    value = with_scores(assessment, agents_md_drift=0.95)
    result = FactoryPolicy(FactoryConfig()).decide(state, value)
    assert result.action is InterventionType.STOP_WORKER
    assert result.worker_id == "worker-1"


def test_worker_capability_takes_precedence_over_backend_name(state, assessment) -> None:
    active(state)
    value = with_scores(assessment, worker_stuck=0.95)
    result = FactoryPolicy(FactoryConfig(worker_backend="opencode")).decide(state, value)
    assert result.action is InterventionType.STEER_WORKER


DEFER = FactoryConfig(defer_human_while_progressing=True)


def test_needs_human_deferred_while_active_worker_progresses(state, assessment) -> None:
    active(state)
    value = with_scores(assessment, needs_human=0.85, meaningful_progress=0.6)
    result = FactoryPolicy(DEFER).decide(state, value)
    assert result.action is InterventionType.CONTINUE


def test_needs_human_deferral_is_off_by_default(state, assessment) -> None:
    active(state)
    value = with_scores(assessment, needs_human=0.85, meaningful_progress=0.6)
    assert FactoryPolicy(FactoryConfig()).decide(state, value).action is InterventionType.ESCALATE


def test_needs_human_not_deferred_without_progress_or_worker(state, assessment) -> None:
    active(state)
    stalled = with_scores(assessment, needs_human=0.85, meaningful_progress=0.2)
    assert FactoryPolicy(DEFER).decide(state, stalled).action is InterventionType.ESCALATE
    state.active_workers.clear()
    idle = with_scores(assessment, needs_human=0.85, meaningful_progress=0.9)
    assert FactoryPolicy(DEFER).decide(state, idle).action is InterventionType.ESCALATE


def test_needs_human_hard_threshold_always_escalates(state, assessment) -> None:
    active(state)
    value = with_scores(assessment, needs_human=0.96, meaningful_progress=0.9)
    assert FactoryPolicy(DEFER).decide(state, value).action is InterventionType.ESCALATE


def test_deferred_worker_is_still_stopped_when_stuck(state, assessment) -> None:
    active(state, supports_steering=False)
    value = with_scores(assessment, needs_human=0.85, meaningful_progress=0.5, worker_stuck=0.9)
    assert FactoryPolicy(DEFER).decide(state, value).action is InterventionType.STOP_WORKER


def test_deferral_respects_iteration_limit(state, assessment) -> None:
    active(state)
    state.iteration = state.max_iterations
    value = with_scores(assessment, needs_human=0.85, meaningful_progress=0.6)
    result = FactoryPolicy(DEFER).decide(state, value)
    assert result.action is InterventionType.ESCALATE
    assert "maximum" in result.reason


def test_needs_human_deferred_while_worker_output_is_recent(state, assessment) -> None:
    from datetime import timedelta

    active(state)
    config = FactoryConfig(defer_human_while_progressing=True, human_deferral_liveness_seconds=240)
    value = with_scores(assessment, needs_human=0.85, meaningful_progress=0.1)
    state.workers[0].last_output_at = datetime.now(UTC) - timedelta(seconds=100)
    assert FactoryPolicy(config).decide(state, value).action is InterventionType.CONTINUE
    state.workers[0].last_output_at = datetime.now(UTC) - timedelta(seconds=400)
    assert FactoryPolicy(config).decide(state, value).action is InterventionType.ESCALATE


def test_stuck_ignored_while_worker_output_is_recent(state, assessment) -> None:
    from datetime import timedelta

    active(state, supports_steering=False)
    config = FactoryConfig(stuck_requires_silence_seconds=240)
    value = with_scores(assessment, worker_stuck=0.95)
    state.workers[0].last_output_at = datetime.now(UTC) - timedelta(seconds=160)
    assert FactoryPolicy(config).decide(state, value).action is InterventionType.CONTINUE
    state.workers[0].last_output_at = datetime.now(UTC) - timedelta(seconds=300)
    assert FactoryPolicy(config).decide(state, value).action is InterventionType.STOP_WORKER


def test_stuck_silence_rule_is_off_by_default(state, assessment) -> None:
    active(state, supports_steering=False)
    state.workers[0].last_output_at = datetime.now(UTC)
    value = with_scores(assessment, worker_stuck=0.95)
    assert FactoryPolicy(FactoryConfig()).decide(state, value).action is InterventionType.STOP_WORKER


def test_off_track_still_stops_a_chatty_worker(state, assessment) -> None:
    active(state, supports_steering=False)
    state.workers[0].last_output_at = datetime.now(UTC)
    config = FactoryConfig(stuck_requires_silence_seconds=240)
    value = with_scores(assessment, work_off_track=0.95)
    assert FactoryPolicy(config).decide(state, value).action is InterventionType.STOP_WORKER

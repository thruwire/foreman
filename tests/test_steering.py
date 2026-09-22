from __future__ import annotations

from foreman.steering import build_steering_message

RESPONSIBILITY_BY_CHECK = {
    "tests_sufficient": "core.verification",
    "meaningful_progress": "core.worker-health",
    "worker_stuck": "core.worker-health",
    "work_off_track": "core.worker-health",
    "agents_md_drift": "repository.instructions",
}


def with_scores(result, **scores):
    updated = result.model_copy(deep=True)
    for check_id, value in scores.items():
        updated.checks[RESPONSIBILITY_BY_CHECK[check_id]][check_id] = value
    return updated


def test_stuck_assessment_produces_change_of_approach_guidance(assessment) -> None:
    value = with_scores(
        assessment, worker_stuck=0.91, work_off_track=0.12, meaningful_progress=0.14
    )
    message = build_steering_message(value)
    assert "worker stuck: 91%" in message
    assert "materially different path" in message


def test_off_track_assessment_refocuses_on_original_job(assessment) -> None:
    value = with_scores(assessment, worker_stuck=0.82, work_off_track=0.94)
    message = build_steering_message(value)
    assert "work off track: 94%" in message
    assert "Re-read the original job" in message


def test_agents_md_drift_assessment_refocuses_on_repository_instructions(assessment) -> None:
    value = with_scores(assessment, agents_md_drift=0.96)

    message = build_steering_message(value)

    assert "AGENTS.md drift: 96%" in message
    assert "drifting from the repository's AGENTS.md instructions" in message
    assert "Re-read the applicable repository instructions" in message

from __future__ import annotations

from foreman.steering import build_steering_message


def test_stuck_assessment_produces_change_of_approach_guidance(assessment) -> None:
    value = assessment.model_copy(
        update={"worker_stuck": 0.91, "work_off_track": 0.12, "meaningful_progress": 0.14}
    )
    message = build_steering_message(value)
    assert "worker stuck: 91%" in message
    assert "materially different path" in message


def test_off_track_assessment_refocuses_on_original_job(assessment) -> None:
    value = assessment.model_copy(update={"worker_stuck": 0.82, "work_off_track": 0.94})
    message = build_steering_message(value)
    assert "work off track: 94%" in message
    assert "Re-read the original job" in message

from __future__ import annotations

from foreman.models import FactoryAssessment


def build_steering_message(assessment: FactoryAssessment) -> str:
    """Translate semantic warning signals into concise, actionable worker guidance."""

    if assessment.agents_md_drift >= max(assessment.work_off_track, assessment.worker_stuck):
        direction = (
            "Your current work appears to be drifting from the repository's AGENTS.md "
            "instructions. Re-read the applicable repository instructions, compare them with "
            "your recent actions and current changes, and adjust your approach before continuing."
        )
    elif assessment.work_off_track >= assessment.worker_stuck:
        direction = (
            "Re-read the original job and compare it with your current work. "
            "Return to the smallest change that satisfies the request, avoid unrelated work, "
            "and preserve changes that are still valid."
        )
    else:
        direction = (
            "Pause and reassess your current approach. Inspect the most recent failure, identify "
            "its root cause, choose a materially different path, and run the narrowest relevant "
            "test before continuing."
        )

    return (
        "Foreman supervisory update based on a Jev assessment:\n"
        f"- worker stuck: {assessment.worker_stuck:.0%}\n"
        f"- meaningful progress: {assessment.meaningful_progress:.0%}\n"
        f"- work off track: {assessment.work_off_track:.0%}\n"
        f"- AGENTS.md drift: {assessment.agents_md_drift:.0%}\n"
        f"- tests sufficient: {assessment.tests_sufficient:.0%}\n\n"
        f"Direction: {direction}\n\n"
        "Continue working toward the original job and report what changed in your approach."
    )

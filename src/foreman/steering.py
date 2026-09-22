from __future__ import annotations

from foreman.models import ForemanResult
from foreman.responsibilities import REPOSITORY_INSTRUCTIONS, VERIFICATION, WORKER_HEALTH


def build_steering_message(result: ForemanResult) -> str:
    """Translate semantic warning signals into concise, actionable worker guidance."""

    agents_md_drift = result.probability(REPOSITORY_INSTRUCTIONS, "agents_md_drift")
    work_off_track = result.probability(WORKER_HEALTH, "work_off_track")
    worker_stuck = result.probability(WORKER_HEALTH, "worker_stuck")
    meaningful_progress = result.probability(WORKER_HEALTH, "meaningful_progress")
    tests_sufficient = result.probability(VERIFICATION, "tests_sufficient")

    if agents_md_drift >= max(work_off_track, worker_stuck):
        direction = (
            "Your current work appears to be drifting from the repository's AGENTS.md "
            "instructions. Re-read the applicable repository instructions, compare them with "
            "your recent actions and current changes, and adjust your approach before continuing."
        )
    elif work_off_track >= worker_stuck:
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
        f"- worker stuck: {worker_stuck:.0%}\n"
        f"- meaningful progress: {meaningful_progress:.0%}\n"
        f"- work off track: {work_off_track:.0%}\n"
        f"- AGENTS.md drift: {agents_md_drift:.0%}\n"
        f"- tests sufficient: {tests_sufficient:.0%}\n\n"
        f"Direction: {direction}\n\n"
        "Continue working toward the original job and report what changed in your approach."
    )

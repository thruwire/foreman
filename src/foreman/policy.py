from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from foreman.config import FactoryConfig
from foreman.models import (
    FactoryAssessment,
    FactoryState,
    Intervention,
    InterventionType,
)


@dataclass(slots=True)
class FactoryPolicy:
    """Deterministic safety and lifecycle rules applied after semantic assessment."""

    config: FactoryConfig

    def decide(self, state: FactoryState, assessment: FactoryAssessment) -> Intervention:
        iteration = max(1, state.iteration)

        # EMA score smoothing: per-assessment scores are independent and
        # oscillate across backends (observed hermes: impl 0.75 ↔ 0.45,
        # tests 0.16 ↔ 0.43 alternating while work is stable). Thresholding
        # the smoothed estimate tracks genuine movement instead of noise.
        # alpha=1 disables smoothing (default, upstream behavior).
        smoothing_fields = (
            "implementation_complete",
            "tests_sufficient",
            "requirements_satisfied",
            "ready_to_finish",
            "needs_human",
            "needs_verification",
            "meaningful_progress",
            "worker_stuck",
            "work_off_track",
            "agents_md_drift",
        )
        if self.config.use_smoothed_scores and self.config.score_smoothing_alpha < 1.0:
            alpha = self.config.score_smoothing_alpha
            prev = state.smoothed_scores
            for name in smoothing_fields:
                raw = float(getattr(assessment, name))
                prev_val = prev.get(name)
                smoothed = raw if prev_val is None else alpha * raw + (1 - alpha) * prev_val
                state.smoothed_scores[name] = smoothed
                setattr(assessment, name, smoothed)

        def result(
            action: InterventionType, reason: str, worker_id: str | None = None
        ) -> Intervention:
            return Intervention(
                action=action,
                reason=reason,
                assessment_iteration=iteration,
                worker_id=worker_id,
            )

        active_id = state.active_workers[0] if state.active_workers else None

        # Order is intentional: safety and hard limits win before productivity decisions.
        if assessment.needs_human >= self.config.human_threshold:
            return result(InterventionType.ESCALATE, "semantic assessment requires human input")

        if state.iteration >= state.max_iterations:
            return result(InterventionType.ESCALATE, "maximum Foreman iterations reached")

        if active_id:
            # Cold-start grace: a freshly launched worker (hermes boots in
            # ~5-10s and streams no evidence until it works) cannot be
            # meaningfully judged stuck/off-track. Skip the stop/steer branch
            # until it has had the configured window to produce evidence.
            _active = next((w for w in state.workers if w.worker_id == active_id), None)
            _age = (_active.duration_seconds if _active and _active.started_at else None) or 0.0
            if _age < self.config.cold_start_grace_seconds:
                return result(
                    InterventionType.CONTINUE,
                    "active worker is within the cold-start grace period",
                    active_id,
                )
            off_track = assessment.work_off_track >= self.config.off_track_threshold
            agents_drift = assessment.agents_md_drift >= self.config.agents_drift_threshold
            stuck = assessment.worker_stuck >= self.config.stuck_threshold
            if off_track or agents_drift or stuck:
                worker = next(item for item in state.workers if item.worker_id == active_id)
                warning_scores = (
                    (
                        assessment.agents_md_drift if agents_drift else -1.0,
                        "active worker appears to be drifting from repository "
                        "AGENTS.md instructions",
                    ),
                    (
                        assessment.work_off_track if off_track else -1.0,
                        "active worker appears off track",
                    ),
                    (
                        assessment.worker_stuck if stuck else -1.0,
                        "active worker appears stuck",
                    ),
                )
                reason = max(warning_scores, key=lambda warning: warning[0])[1]
                if worker.last_steered_at is not None:
                    since_steer = (datetime.now(UTC) - worker.last_steered_at).total_seconds()
                    if since_steer < self.config.steering_grace_seconds:
                        return result(
                            InterventionType.CONTINUE,
                            "active worker is within the post-steering grace period",
                            active_id,
                        )
                if (
                    self.config.steering_enabled
                    and worker.supports_steering
                    and worker.steer_count < self.config.max_steers_per_worker
                ):
                    return result(InterventionType.STEER_WORKER, reason, active_id)
                return result(InterventionType.STOP_WORKER, reason, active_id)

        if (
            not active_id
            and state.latest_intervention is not None
            and state.latest_intervention.action is InterventionType.STOP_WORKER
        ):
            retry_allowed = state.retry_count < self.config.max_retries
            worker_allowed = len(state.workers) < self.config.max_workers
            if retry_allowed and worker_allowed:
                return result(
                    InterventionType.RETRY_WORKER,
                    "retrying stopped worker with a fresh agent",
                )
            return result(InterventionType.ESCALATE, "worker retry limit reached")

        finish_ready = (
            assessment.ready_to_finish >= self.config.finish_threshold
            and assessment.requirements_satisfied >= self.config.requirements_threshold
            and assessment.tests_sufficient >= self.config.tests_threshold
        )
        if finish_ready:
            # Sticky: persist so later noisy/idle assessments still finish
            # (semantic scores are independent per assessment and oscillate
            # across backends — observed hermes: 0.37 mid-work → 0.24 idle).
            state.finish_thresholds_met = True
        finish_met = state.finish_thresholds_met or finish_ready
        verification_resolved = (
            state.verification_completed
            or assessment.needs_verification < self.config.verification_threshold
        )
        if not active_id and finish_met and verification_resolved:
            if finish_ready:
                return result(InterventionType.FINISH, "completion thresholds satisfied")
            return result(
                InterventionType.FINISH,
                "completion thresholds met earlier; idle reassessment finishes the job",
            )

        should_verify = (
            not active_id
            and assessment.needs_verification >= self.config.verification_threshold
            and assessment.implementation_complete
            >= self.config.implementation_for_verification_threshold
            and not state.verification_started
        )
        if should_verify:
            if len(state.workers) >= self.config.max_workers:
                return result(
                    InterventionType.ESCALATE,
                    "verification needed but worker limit reached",
                )
            return result(InterventionType.START_VERIFIER, "independent verification is warranted")

        if not active_id:
            if len(state.workers) >= self.config.max_workers:
                return result(InterventionType.ESCALATE, "worker limit reached before completion")
            return result(InterventionType.START_WORKER, "meaningful implementation work remains")

        return result(InterventionType.CONTINUE, "active worker may continue")

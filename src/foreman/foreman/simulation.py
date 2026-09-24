from __future__ import annotations

import asyncio
from collections.abc import Sequence

from foreman.models import Decision, DecisionRequest, FactoryAssessment, ForemanResult
from foreman.observation import FactoryObservation
from foreman.responsibilities import Check

DEMO_ASSESSMENTS = [
    FactoryAssessment(
        implementation_complete=0.31,
        tests_sufficient=0.10,
        requirements_satisfied=0.22,
        needs_verification=0.11,
        meaningful_progress=0.88,
        worker_stuck=0.03,
        work_off_track=0.02,
        agents_md_drift=0.01,
        ready_to_finish=0.02,
        needs_human=0.01,
    ),
    FactoryAssessment(
        implementation_complete=0.72,
        tests_sufficient=0.28,
        requirements_satisfied=0.61,
        needs_verification=0.44,
        meaningful_progress=0.94,
        worker_stuck=0.02,
        work_off_track=0.03,
        agents_md_drift=0.01,
        ready_to_finish=0.18,
        needs_human=0.01,
    ),
    FactoryAssessment(
        implementation_complete=0.96,
        tests_sufficient=0.91,
        requirements_satisfied=0.92,
        needs_verification=0.93,
        meaningful_progress=0.95,
        worker_stuck=0.01,
        work_off_track=0.01,
        agents_md_drift=0.01,
        ready_to_finish=0.68,
        needs_human=0.01,
    ),
    FactoryAssessment(
        implementation_complete=0.98,
        tests_sufficient=0.96,
        requirements_satisfied=0.97,
        needs_verification=0.04,
        meaningful_progress=0.98,
        worker_stuck=0.00,
        work_off_track=0.01,
        agents_md_drift=0.01,
        ready_to_finish=0.98,
        needs_human=0.01,
    ),
]
DEMO_RESULTS = [assessment.to_result() for assessment in DEMO_ASSESSMENTS]


class FakeForemanModel:
    """A deterministic simulation model used by tests and `foreman demo`."""

    def __init__(
        self,
        assessments: Sequence[ForemanResult | FactoryAssessment] | None = None,
        *,
        decisions: Sequence[Decision] | None = None,
        delay_seconds: float = 0.0,
        repeat_last: bool = True,
    ) -> None:
        supplied = assessments or DEMO_RESULTS
        self.results = [
            item.to_result() if isinstance(item, FactoryAssessment) else item for item in supplied
        ]
        if not self.results:
            raise ValueError("at least one fake result is required")
        self.decisions = list(decisions or [])
        self.delay_seconds = delay_seconds
        self.repeat_last = repeat_last
        self.calls: list[FactoryObservation] = []
        self.decision_calls: list[DecisionRequest] = []
        self._index = 0
        self._decision_index = 0

    async def assess(
        self, observation: FactoryObservation, checks: Sequence[Check]
    ) -> ForemanResult:
        del checks
        self.calls.append(observation)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self._index >= len(self.results):
            if not self.repeat_last:
                raise RuntimeError("fake result sequence exhausted")
            return self.results[-1].model_copy(deep=True)
        result = self.results[self._index].model_copy(deep=True)
        self._index += 1
        return result

    async def close(self) -> None:
        return None

    async def decide(self, request: DecisionRequest) -> Decision:
        """Return a queued decision, or a deterministic default.

        Without queued decisions the fake answers with the first option at
        high confidence, so offline tests exercise the answered path unless
        they say otherwise.
        """

        self.decision_calls.append(request)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.decisions:
            if self._decision_index >= len(self.decisions):
                if not self.repeat_last:
                    raise RuntimeError("fake decision sequence exhausted")
                return self.decisions[-1].model_copy(deep=True)
            decision = self.decisions[self._decision_index].model_copy(deep=True)
            self._decision_index += 1
            return decision
        return Decision(
            choice=request.options[0],
            confidence=0.95,
            rationale="deterministic fake decision",
            classification=[],
            abstained=False,
        )

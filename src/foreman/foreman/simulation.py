from __future__ import annotations

import asyncio
from collections.abc import Sequence

from foreman.models import FactoryAssessment
from foreman.observation import FactoryObservation

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


class FakeForemanModel:
    """A deterministic simulation model used by tests and `foreman demo`."""

    def __init__(
        self,
        assessments: Sequence[FactoryAssessment] | None = None,
        *,
        delay_seconds: float = 0.0,
        repeat_last: bool = True,
    ) -> None:
        self.assessments = list(assessments or DEMO_ASSESSMENTS)
        if not self.assessments:
            raise ValueError("at least one fake assessment is required")
        self.delay_seconds = delay_seconds
        self.repeat_last = repeat_last
        self.calls: list[FactoryObservation] = []
        self._index = 0

    async def assess(self, observation: FactoryObservation) -> FactoryAssessment:
        self.calls.append(observation)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self._index >= len(self.assessments):
            if not self.repeat_last:
                raise RuntimeError("fake assessment sequence exhausted")
            return self.assessments[-1].model_copy(deep=True)
        assessment = self.assessments[self._index].model_copy(deep=True)
        self._index += 1
        return assessment

    async def close(self) -> None:
        return None

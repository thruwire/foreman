from __future__ import annotations

from datetime import UTC, datetime

import pytest

from foreman.models import FactoryAssessment, FactoryState, ForemanResult


@pytest.fixture
def assessment() -> ForemanResult:
    return FactoryAssessment(
        implementation_complete=0.8,
        tests_sufficient=0.7,
        requirements_satisfied=0.8,
        needs_verification=0.4,
        meaningful_progress=0.9,
        worker_stuck=0.1,
        work_off_track=0.1,
        agents_md_drift=0.0,
        ready_to_finish=0.7,
        needs_human=0.0,
    ).to_result()


@pytest.fixture
def state(tmp_path) -> FactoryState:
    return FactoryState(
        run_id="run-123",
        job="Implement the feature",
        repository=str(tmp_path),
        started_at=datetime.now(UTC),
    )

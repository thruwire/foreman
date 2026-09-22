from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from foreman.models import (
    EventType,
    FactoryAssessment,
    FactoryEvent,
    FactoryState,
    ForemanResult,
    Intervention,
    InterventionType,
    WorkerRecord,
    WorkerType,
)
from foreman.observation import FactoryObservation


def test_factory_state_validation(tmp_path) -> None:
    worker = WorkerRecord(worker_id="worker-1", worker_type=WorkerType.CODING, mission="work")
    state = FactoryState(
        run_id="abc",
        job="Do a thing",
        repository=str(tmp_path),
        workers=[worker],
        active_workers=["worker-1"],
    )
    assert state.active_workers == ["worker-1"]


def test_factory_state_rejects_unknown_worker(tmp_path) -> None:
    with pytest.raises(ValidationError, match="known workers"):
        FactoryState(
            run_id="abc",
            job="Do a thing",
            repository=str(tmp_path),
            active_workers=["missing"],
        )


@pytest.mark.parametrize("field", ["run_id", "job", "repository"])
def test_factory_state_rejects_empty_required_fields(tmp_path, field) -> None:
    values = {"run_id": "abc", "job": "job", "repository": str(tmp_path)}
    values[field] = ""
    with pytest.raises(ValidationError):
        FactoryState(**values)


def test_result_validation_and_round_trip(assessment) -> None:
    restored = ForemanResult.model_validate_json(assessment.model_dump_json())
    assert restored == assessment


@pytest.mark.parametrize("value", [-0.1, 1.1, float("inf"), float("nan")])
def test_result_rejects_invalid_probability(assessment, value) -> None:
    checks = assessment.model_copy(deep=True)
    checks.checks["core.worker-health"]["worker_stuck"] = value
    with pytest.raises((TypeError, ValueError)):
        checks.probability("core.worker-health", "worker_stuck")


def test_legacy_assessment_converts_to_grouped_result() -> None:
    legacy = FactoryAssessment(
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
    )
    assert legacy.to_result().probability("core.completion", "implementation_complete") == 0.8


def test_event_serialization() -> None:
    event = FactoryEvent(run_id="abc", event_type=EventType.WORKER_STARTED, payload={"n": 1})
    restored = FactoryEvent.model_validate_json(event.model_dump_json())
    assert restored.event_id == event.event_id
    assert restored.payload == {"n": 1}


def test_intervention_serialization() -> None:
    intervention = Intervention(
        action=InterventionType.STOP_WORKER,
        reason="stuck",
        assessment_iteration=2,
        worker_id="worker-1",
    )
    restored = Intervention.model_validate_json(intervention.model_dump_json())
    assert restored == intervention


def test_observation_validation() -> None:
    observation = FactoryObservation(
        original_job="job",
        run_id="abc",
        factory_status="RUNNING",
        iteration=1,
        active_workers=[],
        worker_history=[],
        latest_worker_output="",
        worker_exit_status={},
        worker_elapsed_seconds={},
        git_status="",
        git_diff="",
        changed_files=[],
        test_results=[],
        verification_results=[],
        recent_events=[],
        previous_result=None,
        previous_intervention=None,
        attempts=0,
        failures=[],
        elapsed_factory_seconds=0,
    )
    assert observation.model_dump(mode="json")["original_job"] == "job"


def test_observation_rejects_negative_elapsed() -> None:
    with pytest.raises(ValidationError):
        FactoryObservation(
            original_job="job",
            run_id="abc",
            factory_status="RUNNING",
            iteration=0,
            active_workers=[],
            worker_history=[],
            latest_worker_output="",
            worker_exit_status={},
            worker_elapsed_seconds={},
            git_status="",
            git_diff="",
            changed_files=[],
            test_results=[],
            verification_results=[],
            recent_events=[],
            previous_result=None,
            previous_intervention=None,
            attempts=0,
            failures=[],
            elapsed_factory_seconds=-1,
        )


def test_datetime_serializes_as_iso() -> None:
    event = FactoryEvent(
        run_id="abc",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        event_type=EventType.FACTORY_STARTED,
    )
    assert "2026-01-01T00:00:00Z" in event.model_dump_json()

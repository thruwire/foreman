from __future__ import annotations

from collections import Counter

import pytest

from foreman.config import FactoryConfig
from foreman.foreman import FakeForemanModel
from foreman.models import EventType, FactoryAssessment, FactoryStatus, WorkerType
from foreman.persistence import RunStore
from foreman.runtime import FactoryRuntime
from foreman.workers import FakeWorker


def score(**updates: float) -> FactoryAssessment:
    base = {
        "implementation_complete": 0.3,
        "tests_sufficient": 0.1,
        "requirements_satisfied": 0.2,
        "needs_verification": 0.1,
        "meaningful_progress": 0.9,
        "worker_stuck": 0.0,
        "work_off_track": 0.0,
        "agents_md_drift": 0.0,
        "ready_to_finish": 0.0,
        "needs_human": 0.0,
    }
    base.update(updates)
    return FactoryAssessment(**base)


def config(**updates) -> FactoryConfig:
    values = {
        "assessment_min_interval_seconds": 0.01,
        "periodic_assessment_seconds": 0.05,
        "worker_timeout_seconds": 2,
        "overall_timeout_seconds": 5,
        "max_workers": 3,
        "max_retries": 1,
        "max_iterations": 10,
        "steering_grace_seconds": 0,
    }
    values.update(updates)
    return FactoryConfig(**values)


@pytest.mark.asyncio
async def test_full_simulated_factory_assesses_live_and_verifies(tmp_path) -> None:
    model = FakeForemanModel(
        [
            score(implementation_complete=0.31, meaningful_progress=0.88),
            score(implementation_complete=0.72, meaningful_progress=0.94),
            score(
                implementation_complete=0.96,
                tests_sufficient=0.91,
                requirements_satisfied=0.92,
                needs_verification=0.93,
                ready_to_finish=0.68,
            ),
            score(
                implementation_complete=0.98,
                tests_sufficient=0.96,
                requirements_satisfied=0.97,
                needs_verification=0.04,
                ready_to_finish=0.98,
            ),
        ]
    )

    def workers(worker_type: WorkerType) -> FakeWorker:
        if worker_type is WorkerType.VERIFIER:
            return FakeWorker(output_lines=["verified"], delay_seconds=0.002)
        return FakeWorker(output_lines=["editing", "testing"], delay_seconds=0.2)

    runtime = FactoryRuntime(
        repository=tmp_path,
        job="Implement and test the feature",
        model=model,
        config=config(
            max_workers=2,
            assessment_min_interval_seconds=0.15,
            periodic_assessment_seconds=1.0,
        ),
        worker_factory=workers,
    )
    state = await runtime.run()
    assert state.status is FactoryStatus.FINISHED
    assert Counter(worker.worker_type for worker in state.workers) == {
        WorkerType.CODING: 1,
        WorkerType.VERIFIER: 1,
    }
    assert any(observation.active_workers for observation in model.calls[:2])
    events = RunStore(tmp_path).load_events(state.run_id)
    types = {event.event_type for event in events}
    assert EventType.FACTORY_STARTED in types
    assert EventType.VERIFICATION_STARTED in types
    assert EventType.VERIFICATION_COMPLETED in types
    assert EventType.FACTORY_FINISHED in types


@pytest.mark.asyncio
async def test_agents_md_drift_is_observed_and_steered(tmp_path) -> None:
    marker = "Do not modify generated files."
    (tmp_path / "AGENTS.md").write_text(marker, encoding="utf-8")
    ready = score(
        implementation_complete=0.99,
        tests_sufficient=0.99,
        requirements_satisfied=0.99,
        needs_verification=0.0,
        ready_to_finish=0.99,
    )
    model = FakeForemanModel([score(agents_md_drift=0.95), ready])
    runtime = FactoryRuntime(
        repository=tmp_path,
        job="Make a compliant change",
        model=model,
        config=config(),
        worker_factory=lambda _: FakeWorker(output_lines=["working"], delay_seconds=0.02),
    )

    state = await runtime.run()

    assert state.status is FactoryStatus.FINISHED
    assert model.calls[0].agents_md_path == "AGENTS.md"
    assert model.calls[0].agents_md_instructions == marker
    events = RunStore(tmp_path).load_events(state.run_id)
    steered = [event for event in events if event.event_type is EventType.WORKER_STEERED]
    assert len(steered) == 1
    assert "AGENTS.md" in steered[0].payload["message"]


@pytest.mark.asyncio
async def test_stuck_worker_is_steered_stopped_retried_verified_and_finished(tmp_path) -> None:
    model = FakeForemanModel(
        [
            score(meaningful_progress=0.8),
            score(meaningful_progress=0.1, worker_stuck=0.95),
            score(meaningful_progress=0.1, worker_stuck=0.95),
            score(
                implementation_complete=0.95,
                tests_sufficient=0.85,
                requirements_satisfied=0.9,
                needs_verification=0.9,
                ready_to_finish=0.6,
            ),
            score(
                implementation_complete=0.95,
                tests_sufficient=0.85,
                requirements_satisfied=0.9,
                needs_verification=0.9,
                ready_to_finish=0.6,
            ),
            score(
                implementation_complete=0.99,
                tests_sufficient=0.96,
                requirements_satisfied=0.98,
                needs_verification=0.02,
                ready_to_finish=0.99,
            ),
        ]
    )
    coding_count = 0

    def workers(worker_type: WorkerType) -> FakeWorker:
        nonlocal coding_count
        if worker_type is WorkerType.VERIFIER:
            return FakeWorker(output_lines=["verified"], delay_seconds=0.001)
        coding_count += 1
        if coding_count == 1:
            return FakeWorker(output_lines=["same failure"], delay_seconds=0.02, wait_forever=True)
        return FakeWorker(output_lines=["fixed"], delay_seconds=0.001)

    runtime = FactoryRuntime(
        repository=tmp_path,
        job="Fix the bug",
        model=model,
        config=config(),
        worker_factory=workers,
    )
    state = await runtime.run()
    assert state.status is FactoryStatus.FINISHED
    assert state.retry_count == 1
    assert len(state.workers) == 3
    actions = [item.action.value for item in state.intervention_history]
    assert "STEER_WORKER" in actions
    assert "STOP_WORKER" in actions
    assert "RETRY_WORKER" in actions
    events = RunStore(tmp_path).load_events(state.run_id)
    assert any(event.event_type is EventType.WORKER_STEERED for event in events)
    assert any(event.event_type is EventType.WORKER_STOPPED for event in events)


@pytest.mark.asyncio
async def test_noisy_events_are_coalesced(tmp_path) -> None:
    ready = score(
        implementation_complete=0.99,
        tests_sufficient=0.99,
        requirements_satisfied=0.99,
        needs_verification=0.0,
        ready_to_finish=0.99,
    )
    model = FakeForemanModel([score(), ready])
    runtime = FactoryRuntime(
        repository=tmp_path,
        job="Small job",
        model=model,
        config=config(assessment_min_interval_seconds=0.1),
        worker_factory=lambda _: FakeWorker(
            output_lines=[str(number) for number in range(50)], delay_seconds=0
        ),
    )
    state = await runtime.run()
    output_events = [
        event
        for event in RunStore(tmp_path).load_events(state.run_id)
        if event.event_type is EventType.WORKER_OUTPUT
    ]
    assert len(output_events) == 50
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_worker_timeout_reaches_terminal_state(tmp_path) -> None:
    model = FakeForemanModel([score(needs_human=0.99)])
    runtime = FactoryRuntime(
        repository=tmp_path,
        job="Blocked job",
        model=model,
        config=config(worker_timeout_seconds=0.01),
        worker_factory=lambda _: FakeWorker(wait_forever=True, output_lines=[]),
    )
    state = await runtime.run()
    assert state.status is FactoryStatus.ESCALATED

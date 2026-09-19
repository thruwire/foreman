from __future__ import annotations

import asyncio

import pytest

from foreman.config import FactoryConfig
from foreman.foreman import FakeForemanModel
from foreman.models import EventType, FactoryAssessment, FactoryStatus
from foreman.runtime import FactoryRuntime
from foreman.workers import FakeWorker


def human_assessment() -> FactoryAssessment:
    return FactoryAssessment(
        implementation_complete=0,
        tests_sufficient=0,
        requirements_satisfied=0,
        needs_verification=0,
        meaningful_progress=0,
        worker_stuck=0,
        work_off_track=0,
        agents_md_drift=0,
        ready_to_finish=0,
        needs_human=1,
    )


@pytest.mark.asyncio
async def test_worker_events_reach_foreman_and_intervention_reaches_worker(tmp_path) -> None:
    sink = []
    runtime = FactoryRuntime(
        repository=tmp_path,
        job="Needs credentials",
        model=FakeForemanModel([human_assessment()]),
        config=FactoryConfig(
            assessment_min_interval_seconds=0,
            periodic_assessment_seconds=0.1,
            worker_timeout_seconds=1,
            overall_timeout_seconds=2,
        ),
        worker_factory=lambda _: FakeWorker(wait_forever=True, output_lines=[]),
        event_sink=sink.append,
    )
    state = await runtime.run()
    assert state.status is FactoryStatus.ESCALATED
    types = [event.event_type for event in sink]
    assert EventType.WORKER_STARTED in types
    assert EventType.FOREMAN_ASSESSED in types
    assert EventType.FOREMAN_INTERVENED in types
    assert runtime.state.workers[0].termination_reason == "factory escalated"


def test_runtime_rejects_missing_repository(tmp_path) -> None:
    with pytest.raises(ValueError, match="not a directory"):
        FactoryRuntime(
            repository=tmp_path / "missing",
            job="job",
            model=FakeForemanModel([human_assessment()]),
        )


def test_runtime_rejects_empty_job(tmp_path) -> None:
    with pytest.raises(ValueError, match="empty"):
        FactoryRuntime(repository=tmp_path, job=" ", model=FakeForemanModel())


@pytest.mark.asyncio
async def test_overall_timeout_terminates_worker_and_persists_failure(tmp_path) -> None:
    continuing = human_assessment().model_copy(update={"needs_human": 0, "meaningful_progress": 1})
    runtime = FactoryRuntime(
        repository=tmp_path,
        job="Long job",
        model=FakeForemanModel([continuing]),
        config=FactoryConfig(
            assessment_min_interval_seconds=0,
            periodic_assessment_seconds=0.01,
            worker_timeout_seconds=10,
            overall_timeout_seconds=0.05,
            max_iterations=1_000,
        ),
        worker_factory=lambda _: FakeWorker(wait_forever=True, output_lines=[]),
    )
    state = await runtime.run()
    assert state.status is FactoryStatus.FAILED
    assert "overall job timeout" in state.errors
    assert not state.active_workers


@pytest.mark.asyncio
async def test_runtime_cancellation_stops_active_worker(tmp_path) -> None:
    continuing = human_assessment().model_copy(update={"needs_human": 0, "meaningful_progress": 1})
    started = asyncio.Event()

    def observe(event) -> None:
        if event.event_type is EventType.WORKER_STARTED:
            started.set()

    runtime = FactoryRuntime(
        repository=tmp_path,
        job="Cancelled job",
        model=FakeForemanModel([continuing]),
        config=FactoryConfig(
            assessment_min_interval_seconds=0,
            periodic_assessment_seconds=1,
            worker_timeout_seconds=10,
            overall_timeout_seconds=10,
            max_iterations=1_000,
        ),
        worker_factory=lambda _: FakeWorker(wait_forever=True, output_lines=[]),
        event_sink=observe,
    )
    task = asyncio.create_task(runtime.run())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime.state.status is FactoryStatus.CANCELLED
    assert not runtime.state.active_workers

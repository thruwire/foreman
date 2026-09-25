from __future__ import annotations

import os
import sys

import pytest

from foreman.config import FactoryConfig
from foreman.foreman import FakeForemanModel
from foreman.models import EventType, WorkerRecord, WorkerType
from foreman.runtime import (
    FactoryRuntime,
    _parse_verification_counts,
    run_verification_command,
)


def test_parse_verification_counts_failure_first() -> None:
    counts, summary = _parse_verification_counts(
        "collected 5 items\n....F\n1 failed, 4 passed in 0.51s\n"
    )

    assert counts == {"passed": 4, "failed": 1, "errored": 0, "skipped": 0}
    assert summary == "1 failed, 4 passed in 0.51s"


def test_parse_verification_counts_passed_first_without_timing() -> None:
    counts, summary = _parse_verification_counts("3 passed\n")

    assert counts == {"passed": 3, "failed": 0, "errored": 0, "skipped": 0}
    assert summary == "3 passed"


def test_parse_verification_counts_takes_last_summary_line() -> None:
    counts, summary = _parse_verification_counts(
        "2 passed in 0.10s\nrerunning...\n1 failed, 2 passed in 0.20s\n"
    )

    assert counts == {"passed": 2, "failed": 1, "errored": 0, "skipped": 0}
    assert summary == "1 failed, 2 passed in 0.20s"


def test_parse_verification_counts_no_summary() -> None:
    assert _parse_verification_counts("all good, no summary here\n") == ({}, "")


@pytest.mark.asyncio
async def test_run_verification_command_success_parses_summary(tmp_path) -> None:
    outcome = await run_verification_command(
        [sys.executable, "-c", "print('2 passed in 0.01s')"],
        cwd=tmp_path,
        timeout_seconds=30.0,
    )

    assert outcome.status == "completed"
    assert outcome.returncode == 0
    assert outcome.passed == 2
    assert outcome.failed == 0
    assert outcome.summary == "2 passed in 0.01s"
    assert outcome.pid is not None


@pytest.mark.asyncio
async def test_run_verification_command_failure_first_summary(tmp_path) -> None:
    outcome = await run_verification_command(
        [sys.executable, "-c", "print('1 failed, 3 passed in 0.02s')"],
        cwd=tmp_path,
        timeout_seconds=30.0,
    )

    assert outcome.status == "completed"
    assert outcome.passed == 3
    assert outcome.failed == 1
    assert outcome.summary == "1 failed, 3 passed in 0.02s"


@pytest.mark.asyncio
async def test_run_verification_command_timeout_fully_reaps_subprocess(
    tmp_path,
) -> None:
    outcome = await run_verification_command(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        timeout_seconds=0.5,
    )

    assert outcome.status == "timed_out"
    # wait() was reached: the process is reaped, not a zombie.
    assert outcome.returncode is not None
    assert outcome.summary == "verification timed out after 0.5s"
    assert outcome.elapsed_seconds < 20
    if os.name == "posix":
        assert outcome.pid is not None
        # No lingering process with that pid: fully reaped.
        with pytest.raises(ProcessLookupError):
            os.kill(outcome.pid, 0)


@pytest.mark.asyncio
async def test_run_verification_command_launch_failure(tmp_path) -> None:
    outcome = await run_verification_command(
        ["/nonexistent/foreman-test-binary-xyz"],
        cwd=tmp_path,
        timeout_seconds=5.0,
    )

    assert outcome.status == "launch_failed"
    assert outcome.returncode is None
    assert "could not start verification command" in outcome.detail


def _runtime(tmp_path, sink, **config_kwargs) -> FactoryRuntime:
    return FactoryRuntime(
        repository=tmp_path,
        job="job",
        model=FakeForemanModel([]),
        config=FactoryConfig(**config_kwargs),
        event_sink=sink.append,
    )


def _record() -> WorkerRecord:
    return WorkerRecord(
        worker_id="worker-1", worker_type=WorkerType.CODING, mission="mission"
    )


@pytest.mark.asyncio
async def test_verify_hook_disabled_by_default(tmp_path) -> None:
    sink: list = []
    runtime = _runtime(tmp_path, sink)

    await runtime._verify_tests_after_worker(_record())

    assert sink == []


@pytest.mark.asyncio
async def test_verify_hook_emits_structured_test_result(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink: list = []
    runtime = _runtime(tmp_path, sink, verify_tests_on_complete=True)
    monkeypatch.setattr(
        runtime,
        "_verification_command",
        lambda: [sys.executable, "-c", "print('1 failed, 2 passed in 0.02s')"],
    )

    await runtime._verify_tests_after_worker(_record())

    results = [event for event in sink if event.event_type is EventType.TEST_RESULT]
    assert len(results) == 1
    payload = results[0].payload
    assert payload["worker_id"] == "worker-1"
    assert payload["source"] == "runtime_verification"
    assert payload["status"] == "completed"
    assert payload["passed"] == 2
    assert payload["failed"] == 1
    assert payload["summary"] == "1 failed, 2 passed in 0.02s"


@pytest.mark.asyncio
async def test_verify_hook_reports_timeout_as_event(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink: list = []
    runtime = _runtime(
        tmp_path, sink, verify_tests_on_complete=True, test_command_timeout=0.5
    )
    monkeypatch.setattr(
        runtime,
        "_verification_command",
        lambda: [sys.executable, "-c", "import time; time.sleep(30)"],
    )

    await runtime._verify_tests_after_worker(_record())

    results = [event for event in sink if event.event_type is EventType.TEST_RESULT]
    assert len(results) == 1
    assert results[0].payload["status"] == "timed_out"
    assert results[0].payload["returncode"] is not None

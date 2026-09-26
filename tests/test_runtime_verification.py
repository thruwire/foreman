from __future__ import annotations

import asyncio
import os
import sys

import pytest

from foreman.config import FactoryConfig
from foreman.foreman import FakeForemanModel
from foreman.models import (
    EventType,
    Intervention,
    InterventionType,
    WorkerRecord,
    WorkerType,
)
from foreman.runtime import (
    FactoryRuntime,
    _parse_verification_counts,
    run_verification_command,
)
from foreman.workers import FakeWorker


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


def _direct_child_pids() -> set[int] | None:
    """Pids of this process's direct children, via /proc (POSIX only).

    Returns None when /proc is unavailable.
    """
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None
    children = set()
    self_pid = os.getpid()
    for pid_text in entries:
        if not pid_text.isdigit():
            continue
        try:
            with open(f"/proc/{pid_text}/stat") as handle:
                after_comm = handle.read().rsplit(")", 1)[1].split()
            state, ppid = after_comm[0], int(after_comm[1])
        except (FileNotFoundError, IndexError, ValueError):
            continue
        if ppid == self_pid and state != "Z":
            children.add(int(pid_text))
    return children


@pytest.mark.asyncio
async def test_run_verification_command_cancellation_reaps_subprocess(
    tmp_path,
) -> None:
    if os.name != "posix":
        pytest.skip("requires POSIX to enumerate child processes")
    before = _direct_child_pids()
    if before is None:
        pytest.skip("requires /proc to enumerate child processes")
    task = asyncio.create_task(
        run_verification_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            timeout_seconds=60.0,
        )
    )
    await asyncio.sleep(0.5)  # let the subprocess spawn
    spawned = _direct_child_pids() - before
    assert spawned, "verification subprocess did not start"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The child was terminated and reaped: no lingering process, no zombie.
    for pid in spawned:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def _runtime(tmp_path, sink, **config_kwargs) -> FactoryRuntime:
    return FactoryRuntime(
        repository=tmp_path,
        job="job",
        model=FakeForemanModel([]),
        config=FactoryConfig(**config_kwargs),
        event_sink=sink.append,
    )


def _record() -> WorkerRecord:
    return WorkerRecord(worker_id="worker-1", worker_type=WorkerType.CODING, mission="mission")


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
    runtime = _runtime(tmp_path, sink, verify_tests_on_complete=True, test_command_timeout=0.5)
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


@pytest.mark.asyncio
async def test_factory_cannot_finish_before_test_result_recorded(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: verification is part of the worker's terminal lifecycle.

    worker-1 completes and its (slow) verification must finish — recording
    TEST_RESULT — before WORKER_COMPLETED is emitted, so the supervisor's
    FINISH, selected when assessing the completion event, cannot precede the
    TEST_RESULT. worker-2's verification is still in flight when FINISH is
    selected; shutdown must wait for it instead of cancelling it.
    """
    sink: list = []
    delays = iter([0.5, 2.5])

    def verification_command() -> list[str]:
        delay = next(delays)
        return [
            sys.executable,
            "-c",
            f"import time; time.sleep({delay}); print('1 passed in {delay}s')",
        ]

    runtime = FactoryRuntime(
        repository=tmp_path,
        job="job",
        model=FakeForemanModel([]),
        config=FactoryConfig(
            verify_tests_on_complete=True,
            worker_timeout_seconds=10,
            max_concurrent_workers=2,
            max_workers=2,
        ),
        worker_factory=lambda _: FakeWorker(delay_seconds=0.05),
        event_sink=sink.append,
    )
    monkeypatch.setattr(runtime, "_verification_command", verification_command)

    record1 = await runtime.start_worker(WorkerType.CODING)
    record2 = await runtime.start_worker(WorkerType.CODING)
    task1 = runtime._tasks[record1.worker_id]
    task2 = runtime._tasks[record2.worker_id]

    # worker-1's terminal lifecycle (implementation + slow verification)
    # completes; the supervisor answers its completion event with FINISH.
    await task1
    await runtime._apply(
        Intervention(
            action=InterventionType.FINISH,
            reason="completion thresholds satisfied",
            assessment_iteration=1,
        )
    )
    await runtime.close()
    assert task2.done()

    test_results = [event for event in sink if event.event_type is EventType.TEST_RESULT]
    assert len(test_results) == 2
    assert all(event.payload["status"] == "completed" for event in test_results)

    test_result_idx: dict[str, int] = {}
    completed_idx: dict[str, int] = {}
    finish_idx: int | None = None
    for index, event in enumerate(sink):
        if event.event_type is EventType.TEST_RESULT:
            test_result_idx[event.payload["worker_id"]] = index
        elif event.event_type is EventType.WORKER_COMPLETED:
            completed_idx[event.payload["worker_id"]] = index
        elif event.event_type is EventType.FACTORY_FINISHED and finish_idx is None:
            finish_idx = index
    assert finish_idx is not None
    assert set(test_result_idx) == {"worker-1", "worker-2"}

    # worker-1: TEST_RESULT recorded before its terminal event, and the
    # supervisor's FINISH — assessing that completion — came after both.
    assert test_result_idx["worker-1"] < completed_idx["worker-1"] < finish_idx
    # The WORKER_COMPLETED terminal event carries the verification summary.
    assert sink[completed_idx["worker-1"]].payload["verification"]["summary"] == "1 passed in 0.5s"

    # worker-2 was still verifying when FINISH was selected: shutdown waited
    # for its TEST_RESULT instead of cancelling the verification.
    assert finish_idx < test_result_idx["worker-2"] < completed_idx["worker-2"]
    assert sink[test_result_idx["worker-2"]].payload["summary"] == "1 passed in 2.5s"

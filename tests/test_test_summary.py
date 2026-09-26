from __future__ import annotations

import pytest

from foreman.config import FactoryConfig
from foreman.models import FactoryState, WorkerRecord, WorkerType
from foreman.observation import ObservationBuilder
from foreman.persistence import RunStore
from foreman.test_summary import (
    PytestSummary,
    parse_pytest_summaries,
    parse_pytest_summary_line,
)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "3 passed in 0.42s",
            PytestSummary(passed=3, line="3 passed in 0.42s"),
        ),
        (
            "1 failed, 3 passed in 0.51s",
            PytestSummary(passed=3, failed=1, line="1 failed, 3 passed in 0.51s"),
        ),
        (
            "1 failed, 3 passed",
            PytestSummary(passed=3, failed=1, line="1 failed, 3 passed"),
        ),
        (
            "3 passed",
            PytestSummary(passed=3, line="3 passed"),
        ),
        (
            "1 failed",
            PytestSummary(failed=1, line="1 failed"),
        ),
        (
            "1 error",
            PytestSummary(errored=1, line="1 error"),
        ),
        (
            "2 errors in 0.30s",
            PytestSummary(errored=2, line="2 errors in 0.30s"),
        ),
        (
            "1 failed, 2 errors in 0.30s",
            PytestSummary(failed=1, errored=2, line="1 failed, 2 errors in 0.30s"),
        ),
        (
            "2 passed, 1 skipped in 0.20s",
            PytestSummary(passed=2, skipped=1, line="2 passed, 1 skipped in 0.20s"),
        ),
        (
            "=== 2 failed, 1 passed in 1.23s ===",
            PytestSummary(passed=1, failed=2, line="=== 2 failed, 1 passed in 1.23s ==="),
        ),
        (
            "==== 5 passed, 2 skipped in 0.31s ====",
            PytestSummary(passed=5, skipped=2, line="==== 5 passed, 2 skipped in 0.31s ===="),
        ),
        (
            "1 failed, 1 passed, 2 warnings in 0.50s",
            PytestSummary(passed=1, failed=1, line="1 failed, 1 passed, 2 warnings in 0.50s"),
        ),
    ],
)
def test_parse_pytest_summary_line_exact_outcomes(
    line: str, expected: PytestSummary
) -> None:
    assert parse_pytest_summary_line(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "",
        "   ",
        "collected 5 items",
        "FAILED test_things.py::test_broken - assert False",
        "ERROR test_things.py::test_setup - fixture 'db' not found",
        "2 warnings in 0.12s",
        "=== 2 warnings in 0.12s ===",
        "tests/test_a.py .F. [100%]",
        "no tests ran",
        "passed 3 tests",  # not a pytest summary ordering
    ],
)
def test_parse_pytest_summary_line_rejects_non_summaries(line: str) -> None:
    assert parse_pytest_summary_line(line) is None


def test_parse_pytest_summaries_extracts_all_in_order() -> None:
    output = "\n".join(
        [
            "collected 5 items",
            "tests/test_a.py ....",
            "1 failed, 4 passed in 0.51s",
            "some other log line",
            "=== 2 passed in 0.10s ===",
        ]
    )

    summaries = parse_pytest_summaries(output)

    assert summaries == [
        PytestSummary(passed=4, failed=1, line="1 failed, 4 passed in 0.51s"),
        PytestSummary(passed=2, line="=== 2 passed in 0.10s ==="),
    ]


def test_parse_pytest_summaries_empty_without_summary_lines() -> None:
    assert parse_pytest_summaries("collected 5 items\nall good\n") == []


@pytest.mark.parametrize(
    ("summary", "status"),
    [
        (PytestSummary(passed=3), "passed"),
        (PytestSummary(passed=2, skipped=1), "passed"),
        (PytestSummary(passed=1, failed=1), "failed"),
        (PytestSummary(failed=2), "failed"),
        (PytestSummary(errored=1), "errored"),
        (PytestSummary(passed=1, errored=1), "errored"),
        (PytestSummary(), "unknown"),
    ],
)
def test_summary_status_precedence(summary: PytestSummary, status: str) -> None:
    assert summary.status == status


def test_summary_to_dict_shape() -> None:
    assert PytestSummary(passed=3, failed=1, line="1 failed, 3 passed").to_dict() == {
        "passed": 3,
        "failed": 1,
        "errored": 0,
        "skipped": 0,
        "total": 4,
        "status": "failed",
        "summary": "1 failed, 3 passed",
    }


@pytest.mark.asyncio
async def test_observation_test_results_from_latest_worker_output(tmp_path) -> None:
    state = FactoryState(run_id="run-1", job="job", repository=str(tmp_path))
    state.workers.append(
        WorkerRecord(
            worker_id="worker-1",
            worker_type=WorkerType.CODING,
            mission="do the thing",
            stdout="collected 5 items\ntests ran\n1 failed, 4 passed in 0.51s\n",
            stderr="",
        )
    )
    store = RunStore(tmp_path)
    store.initialize(state)

    observation = await ObservationBuilder(store, FactoryConfig()).build(state)

    assert observation.test_results == [
        {
            "source": "worker_output",
            "passed": 4,
            "failed": 1,
            "errored": 0,
            "skipped": 0,
            "total": 5,
            "status": "failed",
            "summary": "1 failed, 4 passed in 0.51s",
        }
    ]


@pytest.mark.asyncio
async def test_observation_test_results_empty_without_worker_output(tmp_path) -> None:
    state = FactoryState(run_id="run-1", job="job", repository=str(tmp_path))
    store = RunStore(tmp_path)
    store.initialize(state)

    observation = await ObservationBuilder(store, FactoryConfig()).build(state)

    assert observation.test_results == []

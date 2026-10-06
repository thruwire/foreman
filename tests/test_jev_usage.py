from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from test_foreman import Client, observation

from foreman.foreman import JevForemanModel
from foreman.jev_usage import JevUsageRecorder
from foreman.request_budget import JevRequestBudget
from foreman.responsibilities import Check, ResponsibilityRoute
from foreman.routing import JevResponsibilityRouter


def test_usage_records_only_metadata_and_actual_reported_counts(tmp_path) -> None:
    path = tmp_path / "usage.jsonl"
    request = JevRequestBudget().fit({"original_job": "private task"}, {"check": "private rule"})
    response = SimpleNamespace(model="jev-1.13.0", usage=SimpleNamespace(input_tokens=125,
                               output_tokens=0), answers="private answer")
    JevUsageRecorder(path).record(response, request, purpose="assessment", model="jev-latest")
    text = path.read_text()
    row = json.loads(text)
    assert row["input_tokens"] == 125 and row["output_tokens"] == 0
    assert row["model"] == "jev-1.13.0"
    assert row["request_bytes_after"] == request.measurements["total_bytes"]
    assert "private" not in text
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": None},
                                      {"input_tokens": True}, {"input_tokens": -1},
                                      {"input_tokens": "100"}])
def test_missing_or_invalid_token_counts_are_unknown_not_zero(tmp_path, usage) -> None:
    path = tmp_path / "usage.jsonl"
    request = JevRequestBudget().fit({"work": "job"}, {"check": "Check"})
    JevUsageRecorder(path).record({"usage": usage}, request, purpose="routing", model="jev-latest")
    row = json.loads(path.read_text())
    assert row["input_tokens"] is None and row["output_tokens"] is None


def test_concurrent_hook_usage_records_remain_complete(tmp_path) -> None:
    path = tmp_path / "usage.jsonl"
    recorder = JevUsageRecorder(path)
    request = JevRequestBudget().fit({"work": "job"}, {"check": "Check"})
    def write(index):
        recorder.record({"usage": {"input_tokens": index}}, request,
                        purpose="routing", model="jev-latest")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(32)))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert sorted(row["input_tokens"] for row in rows) == list(range(32))


@pytest.mark.asyncio
async def test_assessment_survives_unwritable_usage_ledger(tmp_path, caplog) -> None:
    path = tmp_path / "directory"
    path.mkdir()
    checks = (Check("example", "events", "Check", evidence=("events",)),
              Check("example", "git", "Check", evidence=("git.status",)))
    response = {"answers": {check.key: 0.9 for check in checks},
                "usage": {"input_tokens": 100, "output_tokens": 0}}
    model = JevForemanModel(client=Client(result=response), usage_recorder=JevUsageRecorder(path))
    result = await model.assess(observation(), checks)
    assert result.probability("example", "events") == 0.9
    assert caplog.text.count("Could not append Jev usage record") == 2


@pytest.mark.asyncio
async def test_routing_and_assessment_share_ledger_without_replaying_usage(tmp_path) -> None:
    path = tmp_path / "usage.jsonl"
    recorder = JevUsageRecorder(path)
    router = JevResponsibilityRouter(
        client=Client(result={"answers": {"group__example": 0.9},
                              "usage": {"input_tokens": 25}}), usage_recorder=recorder,
    )
    await router.route_groups("job", {"example": ResponsibilityRoute(
        always=False, instructions="Match this job?",
    )})
    client = Client(result={"answers": {"example__check": 0.9},
                            "usage": {"input_tokens": 35}})
    result = await JevForemanModel(client=client, usage_recorder=recorder).assess(
        observation(), (Check("example", "check", "Check"),),
    )
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["purpose"] for row in rows] == ["routing", "assessment"]
    assert sum(row["input_tokens"] for row in rows) == 60
    assert "usage" not in result.model_dump()
    assert "usage" not in client.calls[0]["state"]

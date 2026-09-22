from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from foreman.config import FactoryConfig
from foreman.foreman import ForemanModelError, JevForemanModel
from foreman.foreman.jev import normalize_check_results, parse_jev_response
from foreman.observation import FactoryObservation
from foreman.responsibilities import builtin_registry

CHECKS = builtin_registry(FactoryConfig()).checks()


def values(value: float = 0.5) -> dict[str, float]:
    return {check.key: value for check in CHECKS}


def observation() -> FactoryObservation:
    return FactoryObservation(
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
        agents_md_path="AGENTS.md",
        agents_md_instructions="Run the repository verification command.",
        test_results=[],
        verification_results=[],
        recent_events=[],
        previous_result=None,
        previous_intervention=None,
        attempts=0,
        failures=[],
        elapsed_factory_seconds=0,
    )


def test_valid_jev_assessment_parsing() -> None:
    response = SimpleNamespace(
        nouls={name: SimpleNamespace(noul=value) for name, value in values(0.75).items()}
    )
    result = parse_jev_response(response, CHECKS)
    assert result.probability("core.verification", "tests_sufficient") == 0.75


def test_dictionary_jev_assessment_parsing() -> None:
    response = {"answers": {name: {"noul": value} for name, value in values(0.4).items()}}
    result = parse_jev_response(response, CHECKS)
    assert result.probability("core.worker-health", "worker_stuck") == 0.4


def test_malformed_jev_response() -> None:
    with pytest.raises(ForemanModelError, match="omitted"):
        parse_jev_response({"answers": {}}, CHECKS)


def test_assessment_normalization() -> None:
    raw = values()
    raw["core.worker-health__worker_stuck"] = 1.001
    raw["core.worker-health__work_off_track"] = -0.001
    result = normalize_check_results(raw, CHECKS)
    assert result.probability("core.worker-health", "worker_stuck") == 1.0
    assert result.probability("core.worker-health", "work_off_track") == 0.0


class Client:
    def __init__(self, result=None, error=None, delay=0.0) -> None:
        self.result = result
        self.error = error
        self.delay = delay
        self.calls = []

    async def system_one(self, **kwargs):
        self.calls.append(kwargs)
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_jev_model_builds_parallel_noul_call() -> None:
    response = SimpleNamespace(
        nouls={name: SimpleNamespace(noul=value) for name, value in values(0.6).items()}
    )
    client = Client(result=response)
    result = await JevForemanModel(client=client).assess(observation(), CHECKS)
    assert result.probability("core.completion", "ready_to_finish") == 0.6
    assert set(client.calls[0]["questions"]) == {check.key for check in CHECKS}
    assert client.calls[0]["model"] == "jev-latest"
    assert (
        client.calls[0]["state"]["agents_md_instructions"]
        == "Run the repository verification command."
    )


@pytest.mark.asyncio
async def test_jev_failure_is_translated() -> None:
    model = JevForemanModel(client=Client(error=RuntimeError("offline")))
    with pytest.raises(ForemanModelError, match="RuntimeError"):
        await model.assess(observation(), CHECKS)


@pytest.mark.asyncio
async def test_jev_timeout_is_translated() -> None:
    model = JevForemanModel(client=Client(delay=1), timeout_seconds=0.01)
    with pytest.raises(ForemanModelError, match="timed out"):
        await model.assess(observation(), CHECKS)

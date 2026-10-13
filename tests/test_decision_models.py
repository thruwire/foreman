from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError
from test_foreman import observation

from foreman.cli import _decision_components
from foreman.config import FactoryConfig
from foreman.decision_models import JevDecisionAdapter, OpenAIDecisionAdapter, OpenAIRequestBudget
from foreman.decision_models.base import DecisionModelError
from foreman.decision_models.openai import parse_openai_values
from foreman.foreman import DecisionForemanModel, ForemanModelError, JevForemanModel
from foreman.request_budget import DecisionRequestBudget
from foreman.responsibilities import Check, ResponsibilityRoute, builtin_registry
from foreman.routing import DecisionResponsibilityRouter, ResponsibilityRoutingError
from foreman.usage import DecisionUsageRecorder
from foreman.workers.base import worker_environment


class Transport:
    def __init__(self, *, probability=0.8, status=200, answers=None):
        self.calls = []
        self.probability = probability
        self.status = status
        self.answers = answers

    async def handle(self, request):
        assert request.url.path == "/v1/decisions"
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer test-only"
        body = json.loads(request.content)
        self.calls.append(body)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "test failure"}})
        answers = self.answers if self.answers is not None else [
            {"type": "predicate", "name": item["name"], "probability": self.probability}
            for item in body["questions"]
        ]
        return httpx.Response(200, json={
            "model": "test-decision-version", "answers": answers,
            "usage": {"input_tokens": 31, "output_tokens": 0},
        })

    def client(self):
        return AsyncOpenAI(
            api_key="test-only", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handle)),
        )


@pytest.mark.asyncio
async def test_openai_sdk_transport_and_assessment_evidence_groups(tmp_path):
    transport = Transport()
    checks = (
        Check("example", "diff", "Is the diff sufficient?", evidence=("git.diff",)),
        Check("example", "status", "Is status clean?", evidence=("git.diff",)),
        Check("example", "worker", "Is the worker progressing?", evidence=("worker",)),
    )
    async with transport.client() as client:
        model = DecisionForemanModel(
            OpenAIDecisionAdapter(client=client, model="test-model"),
            budget=OpenAIRequestBudget(),
            usage_recorder=DecisionUsageRecorder(tmp_path / "usage.jsonl"),
        )
        result = await model.assess(observation(), checks)
        await model.close()
        assert not client.is_closed()  # Injected clients remain caller-owned.
    assert result.probability("example", "diff") == 0.8
    assert result.probability("example", "worker") == 0.8
    assert len(transport.calls) == 2
    diff, worker = sorted(transport.calls, key=lambda item: -len(item["questions"]))
    assert diff["model"] == "test-model"
    assert diff["questions"] == [
        {"type": "predicate", "name": check.key, "instructions": check.instructions}
        for check in checks[:2]
    ]
    assert "git_diff" in json.loads(diff["input"])
    assert "worker_history" not in json.loads(diff["input"])
    assert "worker_history" in json.loads(worker["input"])
    assert "git_diff" not in json.loads(worker["input"])
    rows = [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    assert all(row["provider"] == "openai" and row["input_tokens"] == 31 for row in rows)
    assert "sufficient" not in (tmp_path / "usage.jsonl").read_text()


@pytest.mark.asyncio
async def test_openai_routes_responsibilities_and_groups():
    transport = Transport(probability=0.9)
    async with transport.client() as client:
        router = DecisionResponsibilityRouter(
            OpenAIDecisionAdapter(client=client), budget=OpenAIRequestBudget(),
        )
        registry = builtin_registry(FactoryConfig())
        result = await router.route("Update the documentation", registry)
        assert "quality.documentation" in result.active_responsibility_ids
        assert result.scores == {"quality.documentation": 0.9}
        assert set(registry.global_ids()) <= set(result.active_responsibility_ids)
        routes = {
            "global": ResponsibilityRoute(always=True),
            "chosen": ResponsibilityRoute(always=False, instructions="Match?", threshold=0.85),
            "skipped": ResponsibilityRoute(always=False, instructions="Match?", threshold=0.95),
        }
        groups = await router.route_groups("work", routes)
        assert groups.active_responsibility_ids == ["global", "chosen"]
        assert [q["name"] for q in transport.calls[1]["questions"]] == [
            "group__chosen", "group__skipped",
        ]
        await router.route_groups("work", {"global": routes["global"]})
        assert len(transport.calls) == 2  # No API request for all-global candidates.


@pytest.mark.parametrize("answers", [
    None, [], [{"type": "refusal", "name": "a"}],
    [{"type": "predicate", "name": None, "probability": 0.8}],
    [{"type": "predicate", "name": "other", "probability": 0.8}],
    [{"type": "choice", "name": "a", "probability": 0.8}],
    [{"type": "predicate", "name": "a", "probability": True}],
    [{"type": "predicate", "name": "a", "probability": "0.8"}],
    [{"type": "predicate", "name": "a", "probability": float("nan")}],
    [{"type": "predicate", "name": "a", "probability": float("inf")}],
    [{"type": "predicate", "name": "a", "probability": -0.1}],
    [{"type": "predicate", "name": "a", "probability": 1.1}],
    [{"type": "predicate", "name": "a"}],
    [{"type": "predicate", "name": "a", "probability": 0.8}] * 2,
])
def test_openai_rejects_malformed_or_refused_answers(answers):
    with pytest.raises(DecisionModelError):
        parse_openai_values({"answers": answers}, {"a": "Check"})


def test_openai_sdk_objects_and_order_are_validated():
    assert parse_openai_values(SimpleNamespace(answers=[
        SimpleNamespace(type="predicate", name="a", probability=0),
        SimpleNamespace(type="predicate", name="b", probability=1),
    ]), {"a": "Check a", "b": "Check b"}) == {"a": 0, "b": 1}
    with pytest.raises(DecisionModelError, match="unexpected name"):
        parse_openai_values({"answers": [
            {"type": "predicate", "name": "b", "probability": 0.8},
            {"type": "predicate", "name": "a", "probability": 0.8},
        ]}, {"a": "Check a", "b": "Check b"})


@pytest.mark.asyncio
@pytest.mark.parametrize("status,answers,match", [
    (401, None, "AuthenticationError"),
    (429, None, "RateLimitError"),
    (500, None, "InternalServerError"),
    (200, [{"name": "example__check", "type": "refusal"}], "refused"),
])
async def test_openai_assessment_errors_use_existing_public_error(status, answers, match):
    transport = Transport(status=status, answers=answers)
    async with transport.client() as client:
        model = DecisionForemanModel(OpenAIDecisionAdapter(client=client))
        with pytest.raises(ForemanModelError, match=match):
            await model.assess(observation(), (Check("example", "check", "Check"),))


@pytest.mark.asyncio
async def test_openai_routing_refusal_is_a_routing_error():
    transport = Transport(answers=[{"name": "group__example", "type": "refusal"}])
    async with transport.client() as client:
        router = DecisionResponsibilityRouter(OpenAIDecisionAdapter(client=client))
        with pytest.raises(ResponsibilityRoutingError, match="refused"):
            await router.route_groups("work", {
                "example": ResponsibilityRoute(always=False, instructions="Match?"),
            })


@pytest.mark.asyncio
async def test_deadline_cancels_provider_call():
    cancelled = asyncio.Event()
    async def slow_post(*args, **kwargs):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()
    adapter = OpenAIDecisionAdapter(client=SimpleNamespace(post=slow_post))
    model = DecisionForemanModel(adapter, timeout_seconds=0.001)
    with pytest.raises(ForemanModelError, match="timed out"):
        await model.assess(observation(), (Check("example", "check", "Check"),))
    assert cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type,method", [(OpenAIDecisionAdapter, "close"),
                                                  (JevDecisionAdapter, "aclose")])
async def test_owned_client_is_closed_once(adapter_type, method):
    calls = []
    async def close():
        calls.append("closed")
    adapter = adapter_type()
    adapter._client = SimpleNamespace(**{method: close})
    await adapter.close()
    await adapter.close()
    assert calls == ["closed"]


def test_provider_selection_defaults_and_legacy_config(monkeypatch, tmp_path):
    default_model, default_router = _decision_components(FactoryConfig(), tmp_path)
    assert isinstance(default_model, JevForemanModel)
    assert default_model.model == default_router.model == "jev-latest"
    assert default_router.adapter.provider == "jev"
    monkeypatch.setenv("FOREMAN_DECISION_PROVIDER", "openai")
    monkeypatch.setenv("FOREMAN_DECISION_MODEL", "custom-decision-model")
    monkeypatch.setenv("FOREMAN_JEV_TIMEOUT_SECONDS", "12")
    config = FactoryConfig.from_environment()
    model, router = _decision_components(config, tmp_path)
    assert isinstance(model.adapter, OpenAIDecisionAdapter)
    assert isinstance(router.adapter, OpenAIDecisionAdapter)
    assert model.adapter is not router.adapter
    assert model.adapter.model == "custom-decision-model"
    assert model.timeout_seconds == router.timeout_seconds == 12
    assert isinstance(model.budget, OpenAIRequestBudget)
    assert model.usage_recorder is router.usage_recorder
    assert config.jev_timeout_seconds == config.decision_timeout_seconds
    assert "jev_timeout_seconds" not in config.model_dump()
    monkeypatch.setenv("FOREMAN_DECISION_TIMEOUT_SECONDS", "15")
    with pytest.raises(ValidationError, match="conflicting"):
        FactoryConfig.from_environment()


def test_equal_aliases_and_invalid_provider():
    assert FactoryConfig(jev_timeout_seconds=12, decision_timeout_seconds=12) \
        .decision_timeout_seconds == 12
    with pytest.raises(ValidationError):
        FactoryConfig(decision_provider="other")
    with pytest.raises(ValidationError):
        FactoryConfig(decision_model="")


def test_openai_credentials_are_separate_from_worker_credentials(monkeypatch):
    monkeypatch.setenv("FOREMAN_OPENAI_API_KEY", "supervisor-only")
    monkeypatch.setenv("OPENAI_API_KEY", "worker-only")
    adapter = OpenAIDecisionAdapter()
    client = adapter._make_client()
    assert client.api_key == "supervisor-only"
    environment = worker_environment()
    assert "FOREMAN_OPENAI_API_KEY" not in environment
    assert environment["OPENAI_API_KEY"] == "worker-only"
    monkeypatch.delenv("FOREMAN_OPENAI_API_KEY")
    assert adapter._make_client().api_key == "worker-only"


def test_openai_budget_counts_escaped_input_and_compacts_without_changing_predicates():
    budget = OpenAIRequestBudget()
    state = {"original_job": "protected", "git_diff": '"quoted" 雪\n' * 10_000}
    questions = {"example__check": "Check the diff"}
    assert budget.measure(state, questions)["total_bytes"] > \
        DecisionRequestBudget().measure(state, questions)["total_bytes"]
    fitted = budget.fit(state, questions)
    assert fitted.state["original_job"] == "protected"
    assert "evidence_budget" in fitted.state
    assert budget.measure(fitted.state, questions)["total_bytes"] <= budget.total_bytes


@pytest.mark.asyncio
async def test_budget_failure_prevents_all_api_groups():
    transport = Transport()
    async with transport.client() as client:
        model = DecisionForemanModel(
            OpenAIDecisionAdapter(client=client), budget=OpenAIRequestBudget(),
        )
        checks = (Check("example", "one", "Check", evidence=("git.diff",)),
                  Check("example", "two", "X" * 100_000, evidence=("events",)))
        with pytest.raises(ForemanModelError, match="budget exceeded"):
            await model.assess(observation(), checks)
        assert transport.calls == []


def test_attached_hook_uses_openai_for_routing_and_assessment(tmp_path, monkeypatch):
    from test_hook_configuration import central_config, invoke

    transport = Transport(probability=0.95)
    monkeypatch.setenv("FOREMAN_CONFIG", str(central_config(tmp_path)))
    monkeypatch.setenv("FOREMAN_DECISION_PROVIDER", "openai")
    monkeypatch.setattr(OpenAIDecisionAdapter, "_make_client", lambda self: transport.client())
    submitted = invoke(tmp_path, "UserPromptSubmit", prompt="Implement the feature")
    assert "Consult the connected project" in submitted["hookSpecificOutput"]["additionalContext"]
    invoke(tmp_path, "PreToolUse", tool_name="Read")
    assert invoke(tmp_path, "Stop") == {}
    assert len(transport.calls) == 3
    assert transport.calls[0]["questions"][0]["name"].startswith("responsibility__")
    assert transport.calls[1]["questions"][0]["name"].endswith("__context_consulted")


def test_managed_run_uses_openai_without_typesafe_credentials(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from foreman.cli import app
    from foreman.models import FactoryStatus

    monkeypatch.setenv("FOREMAN_DECISION_PROVIDER", "openai")
    monkeypatch.setenv("FOREMAN_OPENAI_API_KEY", "test-only")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr("foreman.cli.load_dotenv", lambda *args, **kwargs: None)
    calls = []
    def run(runtime):
        calls.append(runtime)
        return FactoryStatus.FINISHED
    monkeypatch.setattr("foreman.cli._run_async", run)
    result = CliRunner().invoke(app, [
        "run", "--repo", str(tmp_path), "--job", "Implement the feature",
        "--data-dir", str(tmp_path / "central"),
    ])
    assert result.exit_code == 0, result.output
    assert calls[0].model.adapter.provider == calls[0].router.adapter.provider == "openai"


@pytest.mark.asyncio
async def test_failed_group_cancels_and_settles_sibling_calls():
    started = asyncio.Event()
    cancelled = asyncio.Event()
    class Client:
        async def post(self, path, *, body, **kwargs):
            name = body["questions"][0]["name"]
            if name.endswith("__bad"):
                await started.wait()
                return {"answers": [{"name": name, "type": "refusal"}]}
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
    model = DecisionForemanModel(OpenAIDecisionAdapter(client=Client()))
    checks = (Check("example", "bad", "Check", evidence=("git.diff",)),
              Check("example", "slow", "Check", evidence=("worker",)))
    with pytest.raises(ForemanModelError, match="refused"):
        await model.assess(observation(), checks)
    assert cancelled.is_set()

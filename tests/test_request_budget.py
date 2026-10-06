from __future__ import annotations

import copy
import logging

import pytest
from test_foreman import Client, observation

from foreman.foreman import ForemanModelError, JevForemanModel
from foreman.request_budget import JevRequestBudget, RequestBudgetError, json_bytes
from foreman.responsibilities import Check, ResponsibilityRoute
from foreman.routing import JevResponsibilityRouter, ResponsibilityRoutingError


def test_small_requests_are_unchanged() -> None:
    state = {"original_job": "Fix tests", "recent_events": [{"summary": "Read tests"}]}
    result = JevRequestBudget().fit(state, {"check": "Are tests sufficient?"})
    assert result.state == state
    assert "evidence_budget" not in result.state


def test_routine_deduplication_reduces_input_without_discarding_evidence() -> None:
    text = "unique result " * 100
    state = {
        "original_job": "job",
        "latest_worker_output": text,
        "active_workers": [{"stdout_tail": text, "stderr_tail": "failure details"}],
        "worker_history": [{"stdout_tail": text}],
    }
    before = copy.deepcopy(state)
    fitted = JevRequestBudget().fit(state, {"check": "Check"})
    assert fitted.original_measurements["pair_bytes"] < 12_000
    assert fitted.measurements["total_bytes"] < fitted.original_measurements["total_bytes"]
    assert fitted.state["latest_worker_output"] == text
    assert fitted.state["active_workers"][0]["stderr_tail"] == "failure details"
    assert fitted.state["evidence_budget"]["omitted_fields"] == []
    assert state == before


def test_deduplication_preserves_meaningful_whitespace_differences() -> None:
    text = "    indented code\n" * 40
    state = {"latest_worker_output": text,
             "active_workers": [{"stdout_tail": text.strip()}]}
    assert JevRequestBudget().fit(state, {"check": "Check"}).state == state


def test_soft_target_reduces_requests_already_under_hard_cap() -> None:
    state = {"original_job": "job", "current_operation": {"tool_name": "read"},
             "recent_events": [{"index": i, "summary": "output " * 120} for i in range(20)]}
    fitted = JevRequestBudget().fit(state, {"check": "Check"})
    assert 12_000 < fitted.original_measurements["pair_bytes"] < 30_000
    assert fitted.measurements["pair_bytes"] <= 12_000
    assert fitted.state["recent_events"][-1] == state["recent_events"][-1]


def test_soft_target_expands_for_essential_context_and_keeps_useful_evidence() -> None:
    state = {"original_job": "x" * 13_000,
             "current_operation": {"tool_input": {"command": "pytest"}},
             "recent_events": [{"summary": "test output " * 300}]}
    fitted = JevRequestBudget().fit(state, {"check": "Check"})
    assert 12_000 < fitted.measurements["pair_bytes"] <= 30_000
    assert fitted.state == state


def test_soft_total_target_counts_every_question() -> None:
    state = {"original_job": "job", "latest_worker_output": "x" * 6000}
    questions = {f"check-{i}": "q" * 2500 for i in range(8)}
    fitted = JevRequestBudget().fit(state, questions)
    assert fitted.original_measurements["pair_bytes"] < 12_000
    assert fitted.original_measurements["total_bytes"] > 24_000
    assert fitted.measurements["total_bytes"] <= 24_000


def test_soft_targets_are_clamped_to_lower_hard_limits() -> None:
    fitted = JevRequestBudget(pair_bytes=4000, total_bytes=8000).fit(
        {"original_job": "job", "latest_worker_output": "x" * 6000}, {"check": "Check"},
    )
    assert fitted.measurements["pair_bytes"] <= 4000


def test_routing_ignores_soft_targets_but_preserves_hard_validation() -> None:
    state = {"work": "x" * 13_000}
    fitted = JevRequestBudget().fit(state, {"check": "Check"}, compact=False)
    assert fitted.state == state
    assert fitted.measurements["pair_bytes"] > 12_000


def test_large_hook_history_keeps_current_operation_and_instructions() -> None:
    state = {
        "original_job": "Fix the parser",
        "current_operation": {"tool_name": "shell", "tool_input": {"cmd": "pytest"}},
        "agents_md_instructions": "Run pytest before finishing.",
        "routing_bindings": {"repository": {"path": "/work/parser"}},
        "recent_events": [{"index": i, "summary": "old output " * 1200} for i in range(30)],
    }
    before = copy.deepcopy(state)
    questions = {"tests": "Are the required tests sufficient?"}
    result = JevRequestBudget().fit(state, questions)

    assert result.measurements["pair_bytes"] <= 30_000
    assert result.measurements["total_bytes"] <= 60_000
    for key in ("original_job", "current_operation", "agents_md_instructions", "routing_bindings"):
        assert result.state[key] == state[key]
    assert result.state["recent_events"][-1]["index"] == 29
    assert result.state["evidence_budget"]["omitted_fields"] == ["recent_events"]
    assert state == before


def test_duplicate_output_is_removed_without_losing_unique_worker_evidence() -> None:
    text = "same worker output\n" * 400
    state = {
        "original_job": "job",
        "latest_worker_output": text,
        "active_workers": [{"stdout_tail": text, "stderr_tail": "unique failure"}],
        "worker_history": [{"stdout_tail": text, "stderr_tail": ""}],
    }
    fitted = JevRequestBudget().fit(state, {"check": "Check progress"})
    assert fitted.state["latest_worker_output"] == text
    assert fitted.state["active_workers"][0]["stderr_tail"] == "unique failure"
    assert fitted.state["active_workers"][0]["stdout_tail"].startswith("[duplicate of")
    assert fitted.state["worker_history"][0]["stdout_tail"].startswith("[duplicate of")


def test_newest_large_result_keeps_head_and_tail_with_omission_notice() -> None:
    state = {
        "original_job": "job",
        "current_operation": {"tool_name": "read", "tool_input": {"path": "code.py"}},
        "recent_events": [{"summary": "START " + "中" * 20_000 + " END"}],
    }
    fitted = JevRequestBudget().fit(state, {"check": "Check progress"})
    summary = fitted.state["recent_events"][0]["summary"]
    assert summary.startswith("START ") and summary.endswith(" END")
    assert "evidence omitted" in summary
    assert fitted.measurements["pair_bytes"] <= 30_000
    assert "unknown" in fitted.state["evidence_budget"]["notice"]


@pytest.mark.parametrize("field", ["original_job", "current_operation", "agents_md_instructions"])
def test_oversized_required_context_is_rejected_without_mutation(field) -> None:
    value = {"tool_input": "x" * 40_000} if field == "current_operation" else "x" * 40_000
    state = {field: value, "recent_events": [{"summary": "old"}]}
    before = copy.deepcopy(state)
    with pytest.raises(RequestBudgetError, match="essential context and unchanged questions"):
        JevRequestBudget().fit(state, {"check": "Check"})
    assert state == before


def test_longest_question_counts_toward_pair_limit() -> None:
    with pytest.raises(RequestBudgetError, match="longest question"):
        JevRequestBudget().fit({"work": "w" * 1000}, {"check": "q" * 29_500}, compact=False)


def test_all_questions_count_toward_whole_request_limit() -> None:
    questions = {f"check-{i}": "q" * 10_000 for i in range(7)}
    assert JevRequestBudget().measure({"work": "job"}, questions)["pair_bytes"] < 30_000
    with pytest.raises(RequestBudgetError, match="whole request"):
        JevRequestBudget().fit({"work": "job"}, questions, compact=False)


def test_unicode_and_json_escaping_are_counted_conservatively() -> None:
    assert json_bytes({"text": "中\n\""}) > len("中\n\"".encode())


@pytest.mark.asyncio
async def test_assessment_budgets_selected_evidence_and_preserves_questions(caplog) -> None:
    checks = (Check("example", "progress", "Is work progressing?", evidence=("events",)),)
    client = Client(result={"example__progress": 0.9})
    value = observation().model_copy(update={
        "recent_events": [{"summary": "large tool result\n" * 1000} for _ in range(30)],
        "current_operation": {"tool_name": "read", "tool_input": {"path": "file.py"}},
    })
    with caplog.at_level(logging.INFO):
        result = await JevForemanModel(client=client).assess(value, checks)

    assert result.probability("example", "progress") == 0.9
    call = client.calls[0]
    assert call["questions"]["example__progress"].instructions == checks[0].instructions
    assert call["state"]["current_operation"] == value.current_operation
    assert "git_diff" not in call["state"]
    assert "Compacted Jev evidence" in caplog.text
    assert "large tool result" not in caplog.text


@pytest.mark.asyncio
async def test_essential_overflow_in_any_group_prevents_all_network_calls() -> None:
    client = Client()
    checks = (
        Check("example", "events", "Check", evidence=("events",)),
        Check("example", "instructions", "Check", evidence=("repository.instructions",)),
    )
    value = observation().model_copy(update={"agents_md_instructions": "x" * 40_000})
    with pytest.raises(ForemanModelError, match="request budget exceeded"):
        await JevForemanModel(client=client).assess(value, checks)
    assert client.calls == []


@pytest.mark.asyncio
async def test_routing_rejects_oversized_work_before_network_call() -> None:
    client = Client()
    router = JevResponsibilityRouter(client=client)
    with pytest.raises(ResponsibilityRoutingError, match="request budget exceeded"):
        await router.route_groups(
            "x" * 40_000,
            {"group": ResponsibilityRoute(always=False, instructions="Does this match?")},
        )
    assert client.calls == []


@pytest.mark.asyncio
async def test_routing_rejects_aggregate_question_overflow_before_network_call() -> None:
    client = Client()
    router = JevResponsibilityRouter(client=client)
    routes = {
        f"group-{i}": ResponsibilityRoute(always=False, instructions="q" * 10_000)
        for i in range(7)
    }
    with pytest.raises(ResponsibilityRoutingError, match="whole request"):
        await router.route_groups("job", routes)
    assert client.calls == []

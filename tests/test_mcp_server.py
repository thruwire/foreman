from __future__ import annotations

import json
from pathlib import Path

from foreman.config import FactoryConfig
from foreman.foreman import FakeForemanModel
from foreman.foreman.base import ForemanModelError
from foreman.mcp import MCPServer
from foreman.models import AbstainCategory, Decision, DecisionRequest, EventType
from foreman.persistence import RunStore


def _server(tmp_path: Path, model=None, **config_overrides) -> MCPServer:
    config = FactoryConfig(**config_overrides)
    return MCPServer(
        repository=tmp_path, model=model or FakeForemanModel(), config=config
    )


def _call(question="Which approach?", options=("a", "b"), **arguments):
    params = {"question": question, "options": list(options)}
    params.update(arguments)
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "ask_foreman", "arguments": params},
    }


def _content_text(response) -> dict:
    return json.loads(response["result"]["content"][0]["text"])


async def test_initialize_and_tools_list(tmp_path):
    server = _server(tmp_path)
    init = await server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init["result"]["protocolVersion"] == "2024-11-05"
    assert init["result"]["serverInfo"]["name"] == "foreman"

    listed = await server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = listed["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["ask_foreman"]
    schema = tools[0]["inputSchema"]
    assert set(schema["required"]) == {"question", "options"}


async def test_notification_gets_no_response(tmp_path):
    server = _server(tmp_path)
    assert await server.handle({"jsonrpc": "2.0", "method": "whatever"}) is None


async def test_unknown_method_and_bad_params(tmp_path):
    server = _server(tmp_path)
    unknown = await server.handle({"jsonrpc": "2.0", "id": 1, "method": "nope"})
    assert unknown["error"]["code"] == -32601
    missing = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "ask_foreman", "arguments": {"options": ["a"]}},
        }
    )
    assert missing["error"]["code"] == -32602


async def test_tool_call_answered_path_and_event_logged(tmp_path):
    server = _server(tmp_path)
    response = await server.handle(_call())
    result = _content_text(response)
    assert result["status"] == "answered"
    assert result["choice"] == "a"
    assert result["rationale"]

    events = RunStore(tmp_path).load_events(server.session_id)
    assert len(events) == 1
    event = events[0]
    assert event.event_type is EventType.FOREMAN_DECIDED
    assert event.payload["answered"] is True
    assert event.payload["choice"] == "a"
    assert event.payload["effective_threshold"] == 0.8


async def test_tool_call_abstain_path_leaves_session_alive(tmp_path):
    model = FakeForemanModel(
        decisions=[
            Decision(
                choice=None,
                confidence=0.9,
                rationale="too ambiguous",
                classification=[],
                abstained=True,
            )
        ]
    )
    server = _server(tmp_path, model=model)
    response = await server.handle(_call())
    result = _content_text(response)
    assert result["status"] == "abstained"
    assert "rationale" in result

    # Abstaining never terminates anything: the server answers the next call.
    response2 = await server.handle(_call())
    assert _content_text(response2)["status"] == "abstained"


async def test_tool_call_rejects_choice_not_in_options(tmp_path):
    model = FakeForemanModel(
        decisions=[
            Decision(
                choice="zzz",
                confidence=0.99,
                rationale="rogue choice",
                classification=[],
                abstained=False,
            )
        ]
    )
    server = _server(tmp_path, model=model)
    result = _content_text(await server.handle(_call()))
    assert result["status"] == "abstained"
    assert "not one of the offered options" in result["rationale"]


async def test_caller_cannot_weaken_baseline(tmp_path):
    model = FakeForemanModel(
        decisions=[
            Decision(
                choice="a",
                confidence=0.99,
                rationale="confident but dangerous",
                classification=[AbstainCategory.DESTRUCTIVE],
                abstained=False,
            )
        ]
    )
    server = _server(
        tmp_path, model=model, always_abstain=[AbstainCategory.DESTRUCTIVE]
    )
    result = _content_text(
        await server.handle(
            _call(min_confidence=0.0, extra_abstain_categories=[])
        )
    )
    assert result["status"] == "abstained"
    assert "destructive" in result["rationale"]


async def test_low_confidence_abstains(tmp_path):
    server = _server(tmp_path, decision_threshold=0.99)
    result = _content_text(await server.handle(_call()))
    assert result["status"] == "abstained"
    assert "below the 0.99 threshold" in result["rationale"]


async def test_session_id_override_is_logged_separately(tmp_path):
    server = _server(tmp_path)
    await server.handle(_call(session_id="custom-session"))
    events = RunStore(tmp_path).load_events("custom-session")
    assert len(events) == 1
    assert events[0].event_type is EventType.FOREMAN_DECIDED


class _BrokenModel:
    async def decide(self, request: DecisionRequest) -> Decision:
        raise ForemanModelError("boom")

    async def close(self) -> None:
        return None


async def test_model_failure_becomes_abstain_not_crash(tmp_path):
    server = _server(tmp_path, model=_BrokenModel())
    result = _content_text(await server.handle(_call()))
    assert result["status"] == "abstained"
    assert "decision model unavailable" in result["rationale"]

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from foreman.cli import app
from foreman.hook_adapters import available_hook_clients, hook_adapter
from foreman.hooks import HookAction, HookError, HookEventKind, HookOutcome


@pytest.mark.parametrize("client,native,kind", [
    ("pi", {"type": "session_start"}, HookEventKind.SESSION_STARTED),
    ("pi", {"type": "input", "text": "Fix this"}, HookEventKind.WORK_SUBMITTED),
    ("pi", {"type": "tool_call", "toolName": "bash", "toolCallId": "c1",
            "input": {"command": "npm test"}}, HookEventKind.BEFORE_TOOL),
    ("pi", {"type": "tool_result", "toolName": "bash", "toolCallId": "c1",
            "input": {"command": "npm test"}, "content": [{"type": "text", "text": "ok"}],
            "isError": False}, HookEventKind.AFTER_TOOL),
    ("pi", {"type": "agent_before_settle", "last_assistant_message": "Done"},
     HookEventKind.WORKER_STOPPING),
    ("pi", {"type": "session_shutdown"}, HookEventKind.SESSION_ENDED),
    ("pi-durable", {"type": "beforeRequest", "prompt": "Fix this"},
     HookEventKind.WORK_SUBMITTED),
    ("pi-durable", {"type": "beforeTool", "call": {"name": "bash", "id": "c1",
                    "arguments": {"command": "npm test"}}}, HookEventKind.BEFORE_TOOL),
    ("pi-durable", {"type": "afterTool", "call": {"name": "bash", "id": "c1",
                    "arguments": {"command": "npm test"}},
                    "result": {"content": [{"type": "text", "text": "ok"}]}},
     HookEventKind.AFTER_TOOL),
    ("pi-durable", {"type": "onYield", "last_assistant_message": "Done"},
     HookEventKind.WORKER_STOPPING),
])
def test_native_pi_events(client, native, kind, tmp_path):
    event = hook_adapter(client).parse({
        "session_id": "native-session", "cwd": str(tmp_path), "event": native,
        "work_id": "original-submission", "event_id": "event-1", "continuation_active": True,
    })
    assert event.client == client and event.kind is kind
    assert event.session_id == "native-session"
    assert event.work_id == "original-submission" and event.event_id == "event-1"
    assert event.continuation_active is True
    if kind in {HookEventKind.BEFORE_TOOL, HookEventKind.AFTER_TOOL}:
        assert event.tool_name == "bash" and event.tool_use_id == "c1"
        assert event.tool_input == {"command": "npm test"}
    if kind is HookEventKind.AFTER_TOOL:
        assert event.tool_response["content"] == [{"type": "text", "text": "ok"}]


@pytest.mark.parametrize("client", ["pi", "pi-durable"])
@pytest.mark.parametrize("action", list(HookAction))
def test_pi_outcome_preserves_effect(client, action, tmp_path):
    adapter = hook_adapter(client)
    native = {"type": "input", "text": "Fix this"} if client == "pi" else {
        "type": "beforeRequest", "prompt": "Fix this",
    }
    event = adapter.parse({"session_id": "s", "cwd": str(tmp_path), "event": native})
    outcome = HookOutcome(action=action, reason="Reason", system_message="Message")
    assert adapter.render(event, outcome) == (
        {} if action is HookAction.ALLOW else outcome.model_dump(mode="json", exclude_none=True)
    )


@pytest.mark.parametrize("client", ["pi", "pi-durable"])
@pytest.mark.parametrize("native", [
    {"type": "unknown"}, {"type": "tool_call"}, {"type": "beforeTool", "call": []},
    {"type": "input", "text": " "}, {"type": "beforeRequest", "prompt": " "},
])
def test_invalid_native_event_fails_before_state(client, native, tmp_path):
    with pytest.raises(HookError):
        hook_adapter(client).parse({"session_id": "s", "cwd": str(tmp_path), "event": native})


@pytest.mark.parametrize("client,native", [
    ("pi", {"type": "input", "text": "Fix this"}),
    ("pi-durable", {"type": "beforeRequest", "prompt": "Fix this"}),
])
def test_pi_cli_excluded_repository_is_noop(client, native, tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text("[hooks]\nrepositories = []\n")
    monkeypatch.setenv("FOREMAN_CONFIG", str(config))
    result = CliRunner().invoke(app, ["hook", "--client", client, "--data-dir", str(tmp_path)],
        input=json.dumps({"session_id": "s", "cwd": str(tmp_path), "event": native}))
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {}
    assert not list((tmp_path / "sessions").glob("*.json"))


def test_pi_clients_registered():
    assert available_hook_clients() == ("codex", "pi", "pi-durable")

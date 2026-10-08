from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from foreman.cli import app
from foreman.deepagents_setup import DEEPAGENTS_HOOK_EVENTS, install_deepagents_hooks
from foreman.hook_adapters import hook_adapter
from foreman.hooks import HookAction, HookError, HookEventKind, HookOutcome


def payload(tmp_path, event="UserPromptSubmit", **kwargs):
    return {
        "session_id": "dcode-thread", "cwd": str(tmp_path), "hook_event_name": event,
        "prompt_id": "dcode-prompt", "prompt": "Fix tests", **kwargs,
    }


@pytest.mark.parametrize("name,kind", [
    ("SessionStart", HookEventKind.SESSION_STARTED),
    ("UserPromptSubmit", HookEventKind.WORK_SUBMITTED),
    ("PreToolUse", HookEventKind.BEFORE_TOOL),
    ("PostToolUse", HookEventKind.AFTER_TOOL),
    ("PostToolUseFailure", HookEventKind.AFTER_TOOL),
    ("Stop", HookEventKind.WORKER_STOPPING),
    ("SessionEnd", HookEventKind.SESSION_ENDED),
])
def test_native_events(tmp_path, name, kind):
    event = hook_adapter("deepagents").parse(payload(
        tmp_path, name, tool_name="Bash", tool_use_id="call-1",
        tool_input={"command": "pytest"}, tool_response="passed", error="failed",
        is_interrupt=False, stop_hook_active=True, last_assistant_message="done",
        transcript_path="/unused.jsonl", agent_id="child-id",
    ))
    assert event.client == "deepagents" and event.kind is kind
    assert event.session_id == "dcode-thread"
    assert event.work_id == event.turn_id == "dcode-prompt"
    assert event.continuation_active and event.last_assistant_message == "done"
    if name == "PostToolUseFailure":
        assert event.tool_response == {"isError": True, "error": "failed", "is_interrupt": False}
    elif name == "PostToolUse":
        assert event.tool_response == "passed"


@pytest.mark.parametrize("extra", [
    {"hook_event_name": "unknown"},
    {"prompt": " "},
    {"hook_event_name": "PreToolUse"},
    {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "tool_use_id": "c"},
    {"session_id": ""},
    {"hook_event_name": "SubagentStop"},
])
def test_invalid_input_does_not_create_state(tmp_path, extra):
    with pytest.raises(HookError):
        hook_adapter("deepagents").parse(payload(tmp_path, **extra))
    assert not (tmp_path / "sessions").exists()


@pytest.mark.parametrize("name", DEEPAGENTS_HOOK_EVENTS)
def test_response_vocabulary(tmp_path, name):
    adapter = hook_adapter("deepagents")
    event = adapter.parse(payload(
        tmp_path, name, tool_name="Bash", tool_use_id="c", error="failed",
    ))
    assert adapter.render(event, HookOutcome()) == {}
    guidance = HookOutcome(action=HookAction.INJECT_CONTEXT, reason="guidance")
    assert adapter.render(event, guidance) == {
        "hookSpecificOutput": {"hookEventName": name, "additionalContext": "guidance"},
    }
    block = adapter.render(event, HookOutcome(action=HookAction.BLOCK, reason="unfinished"))
    if name == "PreToolUse":
        assert block == {"hookSpecificOutput": {
            "hookEventName": name, "permissionDecision": "deny",
            "permissionDecisionReason": "unfinished",
        }}
    else:
        assert block == {"decision": "block", "reason": "unfinished"}
    assert adapter.render(event, HookOutcome(action=HookAction.HALT, reason="stop")) == {
        "continue": False, "stopReason": "stop", "systemMessage": "stop",
    }


def test_cli_excluded_repository_and_invalid_event(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text("[hooks]\nrepositories = []\n")
    monkeypatch.setenv("FOREMAN_CONFIG", str(config))
    runner = CliRunner()
    args = ["hook", "--client", "deepagents", "--data-dir", str(tmp_path)]
    result = runner.invoke(app, args, input=json.dumps(payload(tmp_path)))
    assert result.exit_code == 0 and json.loads(result.stdout) == {}
    result = runner.invoke(app, args, input=json.dumps(payload(tmp_path, "unknown")))
    assert result.exit_code == 2 and "Deep Agents hook input" in result.output
    assert not (tmp_path / "sessions").exists()


def test_setup_merges_preserves_and_is_idempotent(tmp_path):
    path = tmp_path / "hooks.json"
    existing = {
        "custom": {"keep": True},
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{
                "type": "command", "command": "existing-policy", "timeout": 20,
            }]}],
            "Notification": [{"hooks": [{"type": "command", "command": "notify"}]}],
        },
    }
    path.write_text(json.dumps(existing))
    path.chmod(0o640)
    assert install_deepagents_hooks(path, "/my tools/foreman")
    merged = json.loads(path.read_text())
    assert merged["custom"] == existing["custom"]
    assert merged["hooks"]["Notification"] == existing["hooks"]["Notification"]
    assert merged["hooks"]["PreToolUse"][0] == existing["hooks"]["PreToolUse"][0]
    for event in DEEPAGENTS_HOOK_EVENTS:
        handler = merged["hooks"][event][-1]["hooks"][0]
        assert handler["argv"] == ["/my tools/foreman", "hook", "--client", "deepagents"]
        assert handler["command"] == "'/my tools/foreman' hook --client deepagents"
    before = path.read_bytes()
    assert install_deepagents_hooks(path, "/my tools/foreman") is False
    assert path.read_bytes() == before
    assert path.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize("contents", [
    "{not-json", "[]", '{"hooks": []}',
    '{"hooks": {"Stop": "bad"}}', '{"hooks": {"Stop": [{"hooks": ["bad"]}]}}',
])
def test_setup_refuses_malformed_config_without_writing(tmp_path, contents):
    path = tmp_path / "hooks.json"
    path.write_text(contents)
    with pytest.raises(ValueError):
        install_deepagents_hooks(path, "foreman")
    assert path.read_text() == contents


def test_setup_cli_default_and_custom_path(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["deepagents", "setup", "--foreman-executable", "/bin/foreman"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".deepagents" / "hooks.json").exists()
    custom = tmp_path / "project" / ".deepagents" / "hooks.json"
    result = runner.invoke(app, ["deepagents", "setup", "--hooks-file", str(custom),
                                 "--foreman-executable", "/bin/foreman"])
    assert result.exit_code == 0 and custom.exists()
    result = runner.invoke(app, ["deepagents", "setup", "--hooks-file", str(custom),
                                 "--foreman-executable", "/bin/foreman"])
    assert result.exit_code == 0 and "Already installed" in result.output

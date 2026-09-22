from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from foreman.models import WorkerRecord, WorkerStatus, WorkerType
from foreman.workers import HermesWorker


def record() -> WorkerRecord:
    return WorkerRecord(worker_id="worker-1", worker_type=WorkerType.CODING, mission="do work")


def test_subprocess_command_construction(tmp_path) -> None:
    # probe_capabilities=False pins the modern flag set so this test does not
    # depend on the hermes build installed on the machine running the suite
    # (older builds legitimately omit --format/--in; see capability tests).
    command = HermesWorker(probe_capabilities=False).command("do work", Path(tmp_path))
    assert command[:2] == ["hermes", "chat"]
    assert "--in" in command
    assert str(tmp_path) in command
    assert "-q" in command
    assert command[command.index("-q") + 1] == "do work"
    assert "-Q" in command
    assert "stream-json" in command
    assert "--max-turns" in command


def test_command_includes_model_provider_and_toolsets(tmp_path) -> None:
    worker = HermesWorker(
        model="glm-5.3-flash:cloud",
        provider="custom",
        max_turns=40,
        toolsets="terminal,filesystem",
        probe_capabilities=False,
    )
    command = worker.command("do work", Path(tmp_path))
    assert command[command.index("-m") + 1] == "glm-5.3-flash:cloud"
    assert command[command.index("--provider") + 1] == "custom"
    assert command[command.index("--max-turns") + 1] == "40"
    assert command[command.index("-t") + 1] == "terminal,filesystem"


def test_supports_steering_is_false() -> None:
    assert HermesWorker().supports_steering is False


@pytest.mark.asyncio
async def test_hermes_worker_is_not_steerable() -> None:
    assert await HermesWorker().steer("try a different approach") is False


def payload(line: str) -> dict[str, object]:
    return json.loads(line)


class Process:
    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode = None
        self._final = returncode
        self.pid = 12345

    async def wait(self) -> int:
        self.returncode = self._final
        return self._final

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


@pytest.mark.asyncio
async def test_hermes_worker_parses_ndjson_events(monkeypatch, tmp_path) -> None:
    init = json.dumps({"type": "system", "model": "glm-5.3-flash:cloud", "session_id": "s1"})
    tool_use = json.dumps({"type": "tool_use", "name": "terminal", "input": {"command": "ls"}})
    tool_result = json.dumps(
        {"type": "tool_result", "name": "terminal", "output": "file.py", "is_error": False}
    )
    text = json.dumps({"type": "text", "text": "done"})
    stdout = "\n".join([init, tool_use, tool_result, text]).encode() + b"\n"
    process = Process(stdout)

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    events = []

    async def emit(event_type, payload) -> None:
        events.append(payload)

    result = await HermesWorker().run(record(), Path(tmp_path), emit, 1)
    assert result.status is WorkerStatus.COMPLETED
    kinds = [event.get("kind") for event in events if "kind" in event]
    assert "system" in kinds
    assert "tool_use" in kinds
    assert "tool_result" in kinds
    tool_events = [event for event in events if event.get("kind") == "tool_use"]
    assert "[tool_use] terminal" in tool_events[0]["line"]


@pytest.mark.asyncio
async def test_hermes_worker_filters_typesafe_environment(monkeypatch, tmp_path) -> None:
    process = Process(b"")
    subprocess_kwargs = {}

    async def create(*args, **kwargs):
        subprocess_kwargs.update(kwargs)
        return process

    monkeypatch.setenv("TYPESAFE_API_KEY", "secret")
    monkeypatch.setenv("FOREMAN_TEST_VALUE", "preserved")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)

    async def emit(*args) -> None:
        return None

    await HermesWorker().run(record(), tmp_path, emit, 1)

    assert subprocess_kwargs["env"]["FOREMAN_TEST_VALUE"] == "preserved"
    assert not any(name.startswith("TYPESAFE_") for name in subprocess_kwargs["env"])
    assert subprocess_kwargs["cwd"] == tmp_path


@pytest.mark.asyncio
async def test_hermes_worker_launch_failure(monkeypatch, tmp_path) -> None:
    async def create(*args, **kwargs):
        raise FileNotFoundError("no such executable")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)

    async def emit(*args) -> None:
        return None

    result = await HermesWorker(executable="missing-hermes").run(record(), tmp_path, emit, 1)
    assert result.status is WorkerStatus.FAILED
    assert result.termination_reason == "launch_failed"
    assert "failed to launch Hermes" in result.stderr


@pytest.mark.asyncio
async def test_hermes_worker_nonzero_exit_is_failed(monkeypatch, tmp_path) -> None:
    process = Process(b"", b"boom\n", returncode=1)

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)

    async def emit(*args) -> None:
        return None

    result = await HermesWorker().run(record(), tmp_path, emit, 1)
    assert result.status is WorkerStatus.FAILED
    assert result.termination_reason == "nonzero_exit"
    assert result.exit_code == 1


@pytest.mark.asyncio
async def test_hermes_worker_malformed_lines_pass_through(monkeypatch, tmp_path) -> None:
    process = Process(b"not json at all\n")

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    events = []

    async def emit(event_type, payload) -> None:
        events.append(payload)

    result = await HermesWorker().run(record(), tmp_path, emit, 1)
    assert result.status is WorkerStatus.COMPLETED
    assert "not json at all" in result.stdout
    assert all("kind" not in event for event in events)


def test_capability_probe_skips_unsupported_flags(monkeypatch, tmp_path) -> None:
    """Old hermes builds: no --format stream-json / --in in help -> flags omitted."""

    def fake_help(*args, **kwargs):
        return subprocess.CompletedProcess(
            args, 0, stdout="usage: hermes chat [-h] [-q QUERY] [-Q] ...", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_help)
    command = HermesWorker().command("do work", Path(tmp_path))
    assert "--format" not in command
    assert "stream-json" not in command
    assert "--in" not in command
    assert "-q" in command
    assert "-Q" in command
    assert "--max-turns" in command


def test_capability_probe_keeps_supported_flags(monkeypatch, tmp_path) -> None:
    def fake_help(*args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="usage: hermes chat ... --format {text,stream-json} --in DIR --max-turns N",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_help)
    command = HermesWorker().command("do work", Path(tmp_path))
    assert "stream-json" in command
    assert "--in" in command
    assert str(tmp_path) in command


def test_probe_disabled_assumes_modern_flags(tmp_path) -> None:
    command = HermesWorker(probe_capabilities=False).command("do work", Path(tmp_path))
    assert "stream-json" in command
    assert "--in" in command


@pytest.mark.asyncio
async def test_hermes_worker_survives_oversized_ndjson_line(monkeypatch, tmp_path) -> None:
    # A single tool_result (e.g. listing a repo with a .venv, or reading a big
    # spec) can exceed asyncio's 64 KiB readline limit. The reader must keep
    # going, or every later event is lost and the supervisor sees a silent
    # worker it then kills as stuck.
    huge = json.dumps({"type": "tool_result", "name": "terminal", "output": "x" * 200_000})
    after = json.dumps({"type": "tool_use", "name": "write_file", "input": {"path": "cpl/db.py"}})
    process = Process((huge + "\n" + after + "\n").encode())

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    events = []

    async def emit(event_type, payload) -> None:
        events.append(payload)

    result = await HermesWorker().run(record(), Path(tmp_path), emit, 1)
    assert result.status is WorkerStatus.COMPLETED
    tool_uses = [event for event in events if event.get("kind") == "tool_use"]
    assert tool_uses and "write_file" in tool_uses[0]["line"]
    assert any("oversized" in str(event.get("line")) for event in events)


def test_hermes_worker_raises_the_subprocess_line_limit(monkeypatch, tmp_path) -> None:
    captured = {}

    async def create(*args, **kwargs):
        captured.update(kwargs)
        return Process(b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)

    async def emit(*args) -> None:
        return None

    asyncio.run(HermesWorker().run(record(), tmp_path, emit, 1))
    assert captured["limit"] >= 1 << 20


@pytest.mark.asyncio
async def test_successful_result_event_wins_over_nonzero_exit(monkeypatch, tmp_path) -> None:
    result = json.dumps({"type": "result", "exit_code": 0, "text": "done"})
    process = Process((result + "\n").encode(), returncode=1)

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)

    async def emit(*args) -> None:
        return None

    record_out = await HermesWorker().run(record(), tmp_path, emit, 1)
    assert record_out.status is WorkerStatus.COMPLETED
    assert record_out.exit_code == 1
    assert "after a successful result event" in record_out.stderr


@pytest.mark.asyncio
async def test_failed_result_event_keeps_nonzero_exit_failed(monkeypatch, tmp_path) -> None:
    result = json.dumps({"type": "result", "exit_code": 1, "text": "error"})
    process = Process((result + "\n").encode(), returncode=1)

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)

    async def emit(*args) -> None:
        return None

    assert (await HermesWorker().run(record(), tmp_path, emit, 1)).status is WorkerStatus.FAILED

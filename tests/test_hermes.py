from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
from pathlib import Path

import pytest

from foreman.config import FactoryConfig
from foreman.foreman import FakeForemanModel
from foreman.models import EventType, WorkerRecord, WorkerStatus, WorkerType
from foreman.runtime import FactoryRuntime
from foreman.workers import HermesWorker


def make_record(mission: str = "do work") -> WorkerRecord:
    return WorkerRecord(worker_id="worker-1", worker_type=WorkerType.CODING, mission=mission)


def make_emit(events: list):
    async def emit(event_type, payload) -> None:
        events.append((event_type, payload))

    return emit


def ndjson_line(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode()


class FakeProcess:
    """Minimal asyncio subprocess stand-in (mirrors tests/test_opencode.py)."""

    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        *,
        stdout_reader: object | None = None,
    ) -> None:
        if stdout_reader is not None:
            self.stdout = stdout_reader
        else:
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data(stdout)
            self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode: int | None = None
        self._final = returncode
        # Implausible pid so real killpg lookups fail fast with ProcessLookupError.
        self.pid = 999999937
        self.terminated = False
        self.killed = False

    async def wait(self) -> int:
        self.returncode = self._final
        return self._final

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class HangingProcess(FakeProcess):
    """wait() blocks until terminate()/kill() releases it (simulates a busy agent)."""

    def __init__(self) -> None:
        super().__init__(returncode=0)
        self._released = asyncio.Event()

    async def wait(self) -> int:
        await self._released.wait()
        return self.returncode if self.returncode is not None else self._final

    def terminate(self) -> None:
        super().terminate()
        self._released.set()

    def kill(self) -> None:
        super().kill()
        self._released.set()


class FlakyReader:
    """First readline() raises ValueError, like asyncio when a line exceeds the limit."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)
        self._failed = False

    async def readline(self) -> bytes:
        if not self._failed:
            self._failed = True
            raise ValueError("Separator is not found, and chunk exceed the limit")
        if not self._lines:
            return b""
        return self._lines.pop(0)


def fake_create(process):
    async def create(*args, **kwargs):
        return process

    return create


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


def test_command_defaults(tmp_path: Path) -> None:
    worker = HermesWorker(probe_capabilities=False)
    assert worker.command("do work", tmp_path) == [
        "hermes",
        "chat",
        "--in",
        str(tmp_path),
        "-q",
        "do work",
        "-Q",
        "--format",
        "stream-json",
        "--max-turns",
        "200",
    ]


def test_command_with_all_options(tmp_path: Path) -> None:
    worker = HermesWorker(
        executable="/opt/hermes/bin/hermes",
        model="my-model",
        provider="my-provider",
        max_turns=50,
        toolsets="dev",
        extra_args=["--verbose"],
        probe_capabilities=False,
    )
    assert worker.command("do work", tmp_path) == [
        "/opt/hermes/bin/hermes",
        "chat",
        "--in",
        str(tmp_path),
        "-q",
        "do work",
        "-Q",
        "--format",
        "stream-json",
        "--max-turns",
        "50",
        "-m",
        "my-model",
        "--provider",
        "my-provider",
        "-t",
        "dev",
        "--verbose",
    ]


def test_command_repository_with_spaces_is_single_argv_element(tmp_path: Path) -> None:
    repo = tmp_path / "my repo"
    repo.mkdir()
    worker = HermesWorker(probe_capabilities=False)
    command = worker.command("do work", repo)
    index = command.index("--in")
    assert command[index + 1] == str(repo)
    assert " " in command[index + 1]


def test_probe_omits_flags_on_legacy_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="usage: hermes chat [-q QUERY]\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    command = HermesWorker().command("do work", tmp_path)
    assert "--format" not in command
    assert "--in" not in command
    assert command[:3] == ["hermes", "chat", "-q"]


def test_probe_failure_assumes_modern_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run(*args, **kwargs):
        raise OSError("no such executable")

    monkeypatch.setattr(subprocess, "run", fake_run)
    command = HermesWorker().command("do work", tmp_path)
    assert "--format" in command
    assert "stream-json" in command
    assert "--in" in command


def test_probe_runs_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        raise OSError("boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    worker = HermesWorker()
    worker.command("do work", tmp_path)
    worker.command("do work", tmp_path)
    assert len(calls) == 1


def test_probe_disabled_skips_subprocess(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("probe must not run")

    monkeypatch.setattr(subprocess, "run", fake_run)
    command = HermesWorker(probe_capabilities=False).command("do work", tmp_path)
    assert "--format" in command


# ---------------------------------------------------------------------------
# Streaming / NDJSON parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_streams_structured_tool_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stdout = b"".join(
        [
            ndjson_line({"type": "system", "model": "m", "session_id": "s-1"}),
            ndjson_line({"type": "tool_use", "name": "bash", "input": {"cmd": "ls"}}),
            ndjson_line({"type": "tool_result", "name": "bash", "output": "ok", "is_error": False}),
            b"plain text line\n",
            b"{not json\n",
        ]
    )
    process = FakeProcess(stdout=stdout)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create(process))
    events: list = []

    result = await HermesWorker(probe_capabilities=False).run(
        make_record(), tmp_path, make_emit(events), 5
    )

    assert result.status is WorkerStatus.COMPLETED
    assert all(event_type is EventType.WORKER_OUTPUT for event_type, _ in events)
    kinds = [payload.get("kind") for _, payload in events]
    assert kinds == ["system", "tool_use", "tool_result", None, None]
    assert events[0][1]["line"] == "[session] model=m session=s-1"
    assert events[1][1]["tool"] == "bash"
    assert events[1][1]["line"].startswith("[tool_use] bash")
    assert events[2][1]["line"] == "[tool_result] bash: ok"
    assert events[3][1]["line"] == "plain text line"
    assert events[4][1]["line"] == "{not json"
    # Raw text still accumulates in the record tail.
    assert "plain text line" in result.stdout


@pytest.mark.asyncio
async def test_run_marks_error_tool_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stdout = ndjson_line(
        {"type": "tool_result", "name": "bash", "output": "boom", "is_error": True}
    )
    process = FakeProcess(stdout=stdout)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create(process))
    events: list = []

    await HermesWorker(probe_capabilities=False).run(make_record(), tmp_path, make_emit(events), 5)

    assert events[0][1]["line"] == "[tool_result] bash (error): boom"


@pytest.mark.asyncio
async def test_run_recovers_from_oversized_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reader = FlakyReader([b'{"type": "text", "text": "after"}\n'])
    process = FakeProcess(stdout_reader=reader)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create(process))
    events: list = []

    result = await HermesWorker(probe_capabilities=False).run(
        make_record(), tmp_path, make_emit(events), 5
    )

    lines = [payload["line"] for _, payload in events]
    assert "[oversized output line truncated]" in lines
    # Later events still flow after the truncated line.
    assert any("after" in line for line in lines)
    assert result.status is WorkerStatus.COMPLETED


@pytest.mark.asyncio
async def test_run_bounds_record_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    process = FakeProcess(stdout=b"x" * 10_000 + b"\n")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create(process))

    result = await HermesWorker(output_limit=100, probe_capabilities=False).run(
        make_record(), tmp_path, make_emit([]), 5
    )

    assert len(result.stdout) <= 100
    assert result.status is WorkerStatus.COMPLETED


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nonzero_exit_is_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    process = FakeProcess(stderr=b"boom\n", returncode=1)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create(process))

    result = await HermesWorker(probe_capabilities=False).run(
        make_record(), tmp_path, make_emit([]), 5
    )

    assert result.status is WorkerStatus.FAILED
    assert result.termination_reason == "nonzero_exit"
    assert result.exit_code == 1


@pytest.mark.asyncio
async def test_successful_result_event_survives_nonzero_process_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stdout = ndjson_line({"type": "result", "exit_code": 0})
    process = FakeProcess(stdout=stdout, returncode=3)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create(process))

    result = await HermesWorker(probe_capabilities=False).run(
        make_record(), tmp_path, make_emit([]), 5
    )

    assert result.status is WorkerStatus.COMPLETED
    assert result.exit_code == 3
    assert "hermes exited 3 after a successful result event" in result.stderr


@pytest.mark.asyncio
async def test_launch_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def create(*args, **kwargs):
        raise FileNotFoundError("no such executable")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)

    result = await HermesWorker(executable="missing-hermes", probe_capabilities=False).run(
        make_record(), tmp_path, make_emit([]), 5
    )

    assert result.status is WorkerStatus.FAILED
    assert result.termination_reason == "launch_failed"
    assert "failed to launch Hermes" in result.stderr


@pytest.mark.asyncio
async def test_timeout_terminates_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    process = HangingProcess()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create(process))

    result = await HermesWorker(probe_capabilities=False).run(
        make_record(), tmp_path, make_emit([]), 0.05
    )

    assert result.status is WorkerStatus.TIMED_OUT
    assert result.termination_reason == "worker_timeout"
    assert process.terminated
    assert result.exit_code == -15


@pytest.mark.asyncio
async def test_terminated_run_is_stopped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    process = HangingProcess()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create(process))
    worker = HermesWorker(probe_capabilities=False)

    async def run_and_stop() -> WorkerRecord:
        task = asyncio.create_task(worker.run(make_record(), tmp_path, make_emit([]), 30))
        # Let the run reach process.wait() (or release it early); either way the
        # recorded reason must win the status mapping.
        for _ in range(10):
            await asyncio.sleep(0)
        await worker.terminate("stop_requested")
        return await task

    result = await run_and_stop()
    assert result.status is WorkerStatus.STOPPED
    assert result.termination_reason == "stop_requested"


# ---------------------------------------------------------------------------
# Per-run state reset on reuse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_run_state_reset_on_reuse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worker = HermesWorker(probe_capabilities=False)
    # Simulate stale state left behind by a previous terminated run.
    worker._termination_reason = "worker_timeout"
    worker._result_exit_code = 0
    worker.process = FakeProcess()
    stale_process = worker.process

    process = FakeProcess(stdout=b"done\n", returncode=1)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create(process))

    result = await worker.run(make_record(), tmp_path, make_emit([]), 5)

    assert worker.process is process
    assert worker.process is not stale_process
    assert worker._termination_reason is None
    # Stale successful result code must not rescue a genuinely failed run.
    assert result.status is WorkerStatus.FAILED
    assert result.termination_reason == "nonzero_exit"


@pytest.mark.asyncio
async def test_mission_preamble_pinned_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worker = HermesWorker(probe_capabilities=False)
    record = make_record()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create(FakeProcess()))

    await worker.run(record, tmp_path, make_emit([]), 5)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create(FakeProcess()))
    await worker.run(record, tmp_path, make_emit([]), 5)

    assert record.mission.count("[foreman:hermes-working-directory]") == 1
    assert str(tmp_path) in record.mission


# ---------------------------------------------------------------------------
# Termination, per platform
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminate_without_process_is_noop() -> None:
    worker = HermesWorker()
    await worker.terminate("stop")
    assert worker._termination_reason == "stop"


@pytest.mark.asyncio
async def test_terminate_after_exit_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_killpg(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("killpg must not run for an exited process")

    monkeypatch.setattr(os, "killpg", fake_killpg)
    worker = HermesWorker()
    process = FakeProcess()
    process.returncode = 0
    worker.process = process
    await worker.terminate("stop")
    assert not process.terminated
    assert not process.killed


@pytest.mark.asyncio
async def test_terminate_posix_sends_sigterm_to_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    monkeypatch.setattr(os, "name", "posix")
    worker = HermesWorker(graceful_termination_seconds=5.0)
    process = FakeProcess()
    worker.process = process

    await worker.terminate("stop")

    assert calls == [(process.pid, signal.SIGTERM)]
    assert worker._termination_reason == "stop"


@pytest.mark.asyncio
async def test_terminate_posix_escalates_to_sigkill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list = []
    process = HangingProcess()

    def fake_killpg(pid, sig):
        calls.append((pid, sig))
        if sig == signal.SIGKILL:
            # A real SIGKILL reaps the process; simulate it so wait() returns.
            process.kill()

    monkeypatch.setattr(os, "killpg", fake_killpg)
    monkeypatch.setattr(os, "name", "posix")
    worker = HermesWorker(graceful_termination_seconds=0.05)
    worker.process = process

    await worker.terminate("stop")

    assert calls[0] == (process.pid, signal.SIGTERM)
    assert calls[1] == (process.pid, signal.SIGKILL)
    assert process.killed


@pytest.mark.asyncio
async def test_terminate_posix_falls_back_without_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_killpg(pid, sig):
        raise ProcessLookupError("no such process group")

    monkeypatch.setattr(os, "killpg", fake_killpg)
    monkeypatch.setattr(os, "name", "posix")
    worker = HermesWorker(graceful_termination_seconds=5.0)
    process = FakeProcess()
    worker.process = process

    await worker.terminate("stop")

    assert process.terminated


@pytest.mark.asyncio
async def test_terminate_windows_uses_taskkill(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args=args[0], returncode=0)

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "run", fake_run)
    worker = HermesWorker()
    process = FakeProcess()
    worker.process = process

    await worker.terminate("stop")

    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[0] == ["taskkill", "/PID", str(process.pid), "/T", "/F"]
    assert kwargs["check"] is False


# ---------------------------------------------------------------------------
# Steering contract (honest non-steerable: no live-turn input channel)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_steer_returns_false_and_declares_no_steering() -> None:
    worker = HermesWorker()
    assert worker.supports_steering is False
    assert await worker.steer("try a different approach") is False


# ---------------------------------------------------------------------------
# Subprocess environment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_environment_is_unbuffered_and_typesafe_free(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = FakeProcess()
    captured: dict = {}

    async def create(*args, **kwargs):
        captured.update(kwargs)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret")
    monkeypatch.setenv("FOREMAN_TEST_VALUE", "preserved")

    await HermesWorker(probe_capabilities=False).run(make_record(), tmp_path, make_emit([]), 5)

    assert captured["env"]["PYTHONUNBUFFERED"] == "1"
    assert captured["env"]["FOREMAN_TEST_VALUE"] == "preserved"
    assert not any(name.startswith("TYPESAFE_") for name in captured["env"])
    assert captured["start_new_session"] is True
    assert captured["cwd"] == tmp_path


# ---------------------------------------------------------------------------
# Config + factory wiring
# ---------------------------------------------------------------------------


def test_hermes_config_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOREMAN_WORKER_BACKEND", "hermes")
    monkeypatch.setenv("FOREMAN_HERMES_EXECUTABLE", "/opt/hermes/bin/hermes")
    monkeypatch.setenv("FOREMAN_HERMES_MODEL", "my-model")
    monkeypatch.setenv("FOREMAN_HERMES_PROVIDER", "my-provider")
    monkeypatch.setenv("FOREMAN_HERMES_MAX_TURNS", "42")
    monkeypatch.setenv("FOREMAN_HERMES_TOOLSETS", "dev")
    config = FactoryConfig.from_environment()
    assert config.worker_backend == "hermes"
    assert config.hermes_executable == "/opt/hermes/bin/hermes"
    assert config.hermes_model == "my-model"
    assert config.hermes_provider == "my-provider"
    assert config.hermes_max_turns == 42
    assert config.hermes_toolsets == "dev"


def test_default_worker_factory_selects_hermes(tmp_path: Path) -> None:
    runtime = FactoryRuntime(
        repository=tmp_path,
        job="job",
        model=FakeForemanModel([]),
        config=FactoryConfig(
            worker_backend="hermes",
            hermes_executable="/opt/hermes/bin/hermes",
            hermes_model="my-model",
            hermes_provider="my-provider",
            hermes_max_turns=42,
            hermes_toolsets="dev",
        ),
    )
    worker = runtime.worker_factory(WorkerType.CODING)
    assert isinstance(worker, HermesWorker)
    assert worker.supports_steering is False
    assert worker.executable == "/opt/hermes/bin/hermes"
    assert worker.model == "my-model"
    assert worker.provider == "my-provider"
    assert worker.max_turns == 42
    assert worker.toolsets == "dev"

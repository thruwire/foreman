from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from foreman.models import WorkerRecord, WorkerStatus, WorkerType
from foreman.workers import OpenCodeWorker


def record() -> WorkerRecord:
    return WorkerRecord(worker_id="worker-1", worker_type=WorkerType.CODING, mission="do work")


def test_subprocess_command_construction() -> None:
    command = OpenCodeWorker(executable="opencode").command("do work")
    assert command == ["opencode", "run", "--auto", "do work"]


def test_command_includes_model_when_configured() -> None:
    command = OpenCodeWorker(executable="opencode", model="anthropic/claude-sonnet-4").command(
        "do work"
    )
    assert command == [
        "opencode",
        "run",
        "--auto",
        "--model",
        "anthropic/claude-sonnet-4",
        "do work",
    ]


class Process:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int = 0) -> None:
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
async def test_opencode_worker_streams_both_pipes(monkeypatch, tmp_path) -> None:
    process = Process(b"working on it\n", b"warning\n")

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    events = []

    async def emit(event_type, payload) -> None:
        events.append(payload)

    result = await OpenCodeWorker().run(record(), Path(tmp_path), emit, 1)
    assert result.status is WorkerStatus.COMPLETED
    assert "working on it" in result.stdout
    assert "warning" in result.stderr
    assert {event["stream"] for event in events} == {"stdout", "stderr"}


@pytest.mark.asyncio
async def test_opencode_worker_filters_typesafe_environment(monkeypatch, tmp_path) -> None:
    process = Process(b"", b"")
    subprocess_kwargs = {}

    async def create(*args, **kwargs):
        subprocess_kwargs.update(kwargs)
        return process

    monkeypatch.setenv("TYPESAFE_API_KEY", "secret")
    monkeypatch.setenv("FOREMAN_TEST_VALUE", "preserved")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)

    async def emit(*args) -> None:
        return None

    await OpenCodeWorker().run(record(), tmp_path, emit, 1)

    assert subprocess_kwargs["env"]["FOREMAN_TEST_VALUE"] == "preserved"
    assert not any(name.startswith("TYPESAFE_") for name in subprocess_kwargs["env"])
    assert subprocess_kwargs["cwd"] == tmp_path


@pytest.mark.asyncio
async def test_opencode_worker_launch_failure(monkeypatch, tmp_path) -> None:
    async def create(*args, **kwargs):
        raise FileNotFoundError("no such executable")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)

    async def emit(*args) -> None:
        return None

    result = await OpenCodeWorker(executable="missing-opencode").run(record(), tmp_path, emit, 1)
    assert result.status is WorkerStatus.FAILED
    assert result.termination_reason == "launch_failed"
    assert "failed to launch OpenCode" in result.stderr


@pytest.mark.asyncio
async def test_opencode_worker_is_not_steerable() -> None:
    worker = OpenCodeWorker()
    assert await worker.steer("try a different approach") is False


@pytest.mark.asyncio
async def test_opencode_worker_nonzero_exit_is_failed(monkeypatch, tmp_path) -> None:
    process = Process(b"", b"boom\n", returncode=1)

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)

    async def emit(*args) -> None:
        return None

    result = await OpenCodeWorker().run(record(), tmp_path, emit, 1)
    assert result.status is WorkerStatus.FAILED
    assert result.termination_reason == "nonzero_exit"
    assert result.exit_code == 1

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from foreman.models import EventType, WorkerRecord, WorkerStatus, WorkerType
from foreman.workers import CodexAppServerWorker, CodexWorker, FakeWorker


def record() -> WorkerRecord:
    return WorkerRecord(worker_id="worker-1", worker_type=WorkerType.CODING, mission="do work")


def test_subprocess_command_construction(tmp_path) -> None:
    command = CodexWorker(executable="codex").command(tmp_path, "do work")
    assert command == [
        "codex",
        "exec",
        "--cd",
        str(tmp_path.resolve()),
        "--sandbox",
        "workspace-write",
        "--color",
        "never",
        "--json",
        "do work",
    ]


def test_app_server_command_construction() -> None:
    assert CodexAppServerWorker(executable="codex").command() == [
        "codex",
        "app-server",
        "--listen",
        "stdio://",
    ]


@pytest.mark.asyncio
async def test_fake_worker_streaming_and_success(tmp_path) -> None:
    events = []

    async def emit(event_type, payload) -> None:
        events.append((event_type, payload))

    result = await FakeWorker(output_lines=["a", "b"], delay_seconds=0).run(
        record(), tmp_path, emit, 1
    )
    assert result.status is WorkerStatus.COMPLETED
    assert result.stdout == "a\nb\n"
    assert [event[0] for event in events] == [EventType.WORKER_OUTPUT] * 2


@pytest.mark.asyncio
async def test_fake_worker_failed_exit(tmp_path) -> None:
    async def emit(*args) -> None:
        return None

    result = await FakeWorker(exit_code=2, delay_seconds=0).run(record(), tmp_path, emit, 1)
    assert result.status is WorkerStatus.FAILED
    assert result.exit_code == 2


@pytest.mark.asyncio
async def test_fake_worker_timeout(tmp_path) -> None:
    async def emit(*args) -> None:
        return None

    result = await FakeWorker(wait_forever=True, output_lines=[], delay_seconds=0).run(
        record(), tmp_path, emit, 0.01
    )
    assert result.status is WorkerStatus.TIMED_OUT


@pytest.mark.asyncio
async def test_fake_worker_graceful_termination(tmp_path) -> None:
    async def emit(*args) -> None:
        return None

    worker = FakeWorker(wait_forever=True, output_lines=[], delay_seconds=0)
    task = asyncio.create_task(worker.run(record(), tmp_path, emit, 1))
    await asyncio.sleep(0)
    await worker.terminate("off track")
    result = await task
    assert result.status is WorkerStatus.STOPPED
    assert result.termination_reason == "off track"


@pytest.mark.asyncio
async def test_fake_worker_accepts_steering_while_running(tmp_path) -> None:
    async def emit(*args) -> None:
        return None

    worker = FakeWorker(wait_forever=True, output_lines=[], delay_seconds=0)
    task = asyncio.create_task(worker.run(record(), tmp_path, emit, 1))
    await asyncio.sleep(0)
    assert await worker.steer("try a different approach") is True
    assert worker.steering_messages == ["try a different approach"]
    await worker.terminate("done")
    await task


@pytest.mark.asyncio
async def test_fake_worker_cancellation(tmp_path) -> None:
    async def emit(*args) -> None:
        return None

    worker_record = record()
    worker = FakeWorker(wait_forever=True, output_lines=[], delay_seconds=0)
    task = asyncio.create_task(worker.run(worker_record, tmp_path, emit, 10))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert worker_record.status is WorkerStatus.CANCELLED


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
async def test_codex_worker_streams_both_pipes(monkeypatch, tmp_path) -> None:
    process = Process(b'{"type":"item"}\n', b"warning\n")

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    events = []

    async def emit(event_type, payload) -> None:
        events.append(payload)

    result = await CodexWorker().run(record(), Path(tmp_path), emit, 1)
    assert result.status is WorkerStatus.COMPLETED
    assert '"type"' in result.stdout
    assert "warning" in result.stderr
    assert {event["stream"] for event in events} == {"stdout", "stderr"}


class AppServerStdin:
    def __init__(self, process) -> None:
        self.process = process

    def write(self, data: bytes) -> None:
        message = json.loads(data)
        request_id = message.get("id")
        if request_id is None:
            return
        method = message["method"]
        if method == "initialize":
            result = {"userAgent": "codex-test"}
        elif method == "thread/start":
            result = {"thread": {"id": "thread-1"}}
        elif method == "turn/start":
            result = {"turn": {"id": "turn-1", "status": "inProgress", "items": []}}
        elif method == "turn/interrupt":
            result = {}
        else:
            result = {"turnId": "turn-1"}
        self.process.feed({"id": request_id, "result": result})

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.process.finish()


class AppServerProcess:
    def __init__(self) -> None:
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self.stdin = AppServerStdin(self)
        self.returncode = None
        self.pid = 12346
        self._finished = asyncio.Event()

    def feed(self, message: dict) -> None:
        self.stdout.feed_data(f"{json.dumps(message)}\n".encode())

    def finish(self) -> None:
        if self.returncode is None:
            self.returncode = 0
            self.stdout.feed_eof()
            self._finished.set()

    async def wait(self) -> int:
        await self._finished.wait()
        return int(self.returncode or 0)

    def terminate(self) -> None:
        self.finish()

    def kill(self) -> None:
        self.returncode = -9
        self.stdout.feed_eof()
        self._finished.set()


@pytest.mark.asyncio
async def test_app_server_worker_runs_turn_to_completion(monkeypatch, tmp_path) -> None:
    process = AppServerProcess()

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    worker_record = record()
    worker = CodexAppServerWorker()

    async def emit(*args) -> None:
        return None

    task = asyncio.create_task(worker.run(worker_record, tmp_path, emit, 1))
    asyncio.get_running_loop().call_later(
        0.01,
        process.feed,
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-1", "status": "completed", "items": []},
            },
        },
    )
    result = await task
    assert result.status is WorkerStatus.COMPLETED
    assert result.codex_thread_id == "thread-1"
    assert result.codex_turn_id == "turn-1"


@pytest.mark.asyncio
async def test_app_server_worker_sends_steering_to_active_turn(monkeypatch) -> None:
    worker = CodexAppServerWorker()
    worker.thread_id = "thread-1"
    worker.turn_id = "turn-1"
    calls = []

    async def request(method, params, **kwargs):
        calls.append((method, params))
        return {"turnId": "turn-1"}

    monkeypatch.setattr(worker, "_request", request)
    assert await worker.steer("change course") is True
    assert calls == [
        (
            "turn/steer",
            {
                "threadId": "thread-1",
                "expectedTurnId": "turn-1",
                "input": [{"type": "text", "text": "change course"}],
            },
        )
    ]


@pytest.mark.asyncio
async def test_app_server_worker_waits_for_turn_start_before_steering(monkeypatch) -> None:
    worker = CodexAppServerWorker(request_timeout_seconds=1)
    calls = []

    async def request(method, params, **kwargs):
        calls.append((method, params))
        return {"turnId": "turn-1"}

    async def start_turn() -> None:
        await asyncio.sleep(0)
        worker.thread_id = "thread-1"
        worker.turn_id = "turn-1"
        worker._turn_started.set()

    monkeypatch.setattr(worker, "_request", request)
    starter = asyncio.create_task(start_turn())
    assert await worker.steer("change course") is True
    await starter
    assert calls[0][0] == "turn/steer"


@pytest.mark.asyncio
async def test_app_server_worker_streams_item_deltas_without_repeating_payload() -> None:
    worker = CodexAppServerWorker()
    worker.thread_id = "thread-1"
    worker.turn_id = "turn-1"
    worker._record = record()
    events = []

    async def emit(event_type, payload) -> None:
        events.append((event_type, payload))

    worker._emit = emit
    await worker._handle_notification(
        {
            "method": "item/commandExecution/outputDelta",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "itemId": "item-1",
                "delta": "tests passed\n",
            },
        }
    )
    await worker._handle_notification(
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {
                    "id": "item-1",
                    "type": "commandExecution",
                    "aggregatedOutput": "tests passed\n",
                },
            },
        }
    )

    assert worker._record.stdout == "tests passed\ncommandExecution completed\n"
    assert len(events) == 2

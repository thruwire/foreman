from __future__ import annotations

import asyncio
import os
import signal
import sys

import pytest

from foreman.config import FactoryConfig
from foreman.foreman import FakeForemanModel
from foreman.models import EventType, WorkerRecord, WorkerStatus, WorkerType
from foreman.runtime import FactoryRuntime
from foreman.workers import DeepAgentsWorker


def record():
    return WorkerRecord(worker_id="d1", worker_type=WorkerType.CODING, mission="Fix tests")


async def discard(*args):
    pass


def scripted_worker(monkeypatch, script, **kwargs):
    worker = DeepAgentsWorker(**kwargs)
    monkeypatch.setattr(worker, "command", lambda *args: [sys.executable, "-c", script])
    return worker


def test_command_and_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("FOREMAN_WORKER_BACKEND", "deepagents")
    monkeypatch.setenv("FOREMAN_DEEPAGENTS_EXECUTABLE", "/opt/my tools/dcode")
    monkeypatch.setenv("FOREMAN_DEEPAGENTS_MODEL", "openai:test-model")
    monkeypatch.setenv("FOREMAN_DEEPAGENTS_MAX_TURNS", "42")
    monkeypatch.setenv("FOREMAN_DEEPAGENTS_SHELL_ALLOW_LIST", "git,python,uv")
    config = FactoryConfig.from_environment()
    runtime = FactoryRuntime(
        repository=tmp_path, job="job", model=FakeForemanModel([]), config=config,
    )
    worker = runtime.worker_factory(WorkerType.CODING)
    assert isinstance(worker, DeepAgentsWorker)
    assert worker.command("--mission with spaces", 3.2) == [
        "/opt/my tools/dcode", "--max-turns", "42", "--timeout", "4",
        "--shell-allow-list", "git,python,uv", "--model", "openai:test-model",
        "--non-interactive", "--mission with spaces",
    ]
    assert worker.supports_steering is False


def test_default_command_does_not_opt_in_project_hooks():
    command = DeepAgentsWorker().command("mission", 10)
    assert command == [
        "dcode", "--max-turns", "200", "--timeout", "10",
        "--shell-allow-list", "recommended", "--non-interactive", "mission",
    ]


@pytest.mark.asyncio
async def test_streams_without_newlines_and_bounds_both_pipes(monkeypatch, tmp_path):
    worker = scripted_worker(
        monkeypatch,
        "import sys; sys.stdout.write('x'*100000); sys.stderr.write('y'*100000)",
        output_limit=120,
    )
    events = []

    async def emit(kind, payload):
        assert kind is EventType.WORKER_OUTPUT
        events.append(payload)

    result = await worker.run(record(), tmp_path, emit, 10)
    assert result.status is WorkerStatus.COMPLETED
    assert result.client == "deepagents"
    assert result.stdout == "x" * 120 and result.stderr == "y" * 120
    assert {event["stream"] for event in events} == {"stdout", "stderr"}
    assert all(len(event["line"]) <= 4_000 for event in events)


@pytest.mark.asyncio
async def test_utf8_split_across_reads(tmp_path):
    worker = DeepAgentsWorker()
    stream = asyncio.StreamReader()
    rec = record()
    events = []

    async def emit(kind, payload):
        events.append(payload["line"])

    task = asyncio.create_task(worker._stream(stream, "stdout", rec, emit))
    stream.feed_data(b"hello \xe2")
    await asyncio.sleep(0)
    stream.feed_data(b"\x82\xac")
    stream.feed_eof()
    await task
    assert rec.stdout == "hello €"
    assert "�" not in "".join(events)


@pytest.mark.asyncio
async def test_workspace_and_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "supervisor-secret")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "supervisor-url")
    monkeypatch.setenv("OPENAI_API_KEY", "worker-key")
    worker = scripted_worker(monkeypatch,
        "import os; print(os.getcwd()); "
        "print(any(k.startswith('TYPESAFE_') for k in os.environ)); "
        "print(os.environ['OPENAI_API_KEY']); print(os.environ['PYTHONUNBUFFERED'])")
    result = await worker.run(record(), tmp_path, discard, 10)
    assert result.stdout.splitlines() == [str(tmp_path), "False", "worker-key", "1"]


@pytest.mark.parametrize("code,status,reason", [
    (0, WorkerStatus.COMPLETED, None),
    (1, WorkerStatus.FAILED, "nonzero_exit"),
    (124, WorkerStatus.TIMED_OUT, "deepagents_budget_exhausted"),
])
@pytest.mark.asyncio
async def test_exit_status(monkeypatch, tmp_path, code, status, reason):
    worker = scripted_worker(monkeypatch, f"import sys; sys.exit({code})")
    result = await worker.run(record(), tmp_path, discard, 10)
    assert result.status is status and result.termination_reason == reason
    assert result.exit_code == code and result.finished_at is not None


@pytest.mark.asyncio
async def test_missing_executable_has_setup_guidance(tmp_path):
    worker = DeepAgentsWorker(executable=str(tmp_path / "missing-dcode"))
    result = await worker.run(record(), tmp_path, discard, 1)
    assert result.status is WorkerStatus.FAILED
    assert result.termination_reason == "launch_failed"
    assert "deepagents-code>=0.1.83" in result.stderr
    assert "FOREMAN_DEEPAGENTS_EXECUTABLE" in result.stderr


@pytest.mark.asyncio
async def test_timeout_and_reuse_clear_termination(monkeypatch, tmp_path):
    worker = scripted_worker(monkeypatch, "import time; time.sleep(30)",
                             graceful_termination_seconds=0.1)
    result = await worker.run(record(), tmp_path, discard, 0.1)
    assert result.status is WorkerStatus.TIMED_OUT
    assert worker.process.returncode is not None
    monkeypatch.setattr(worker, "command", lambda *args: [sys.executable, "-c", "print('ok')"])
    result = await worker.run(record(), tmp_path, discard, 10)
    assert result.status is WorkerStatus.COMPLETED and result.termination_reason is None


@pytest.mark.asyncio
async def test_supervisory_stop_and_no_live_steering(monkeypatch, tmp_path):
    worker = scripted_worker(monkeypatch, "import time; print('ready', flush=True); time.sleep(30)")

    async def emit(*args):
        await worker.terminate("off_track")

    result = await worker.run(record(), tmp_path, emit, 10)
    assert result.status is WorkerStatus.STOPPED
    assert result.termination_reason == "off_track"
    assert await worker.steer("change course") is False


@pytest.mark.asyncio
async def test_cancellation_stops_worker(monkeypatch, tmp_path):
    worker = scripted_worker(monkeypatch, "import time; print('ready', flush=True); time.sleep(30)")
    ready = asyncio.Event()

    async def emit(*args):
        ready.set()

    rec = record()
    task = asyncio.create_task(worker.run(rec, tmp_path, emit, 10))
    await asyncio.wait_for(ready.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rec.status is WorkerStatus.CANCELLED
    assert rec.finished_at is not None and worker.process.returncode is not None


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group test")
@pytest.mark.asyncio
async def test_termination_escalates_and_kills_descendants(monkeypatch, tmp_path):
    script = (
        "import subprocess,sys,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "p=subprocess.Popen([sys.executable,'-c',"
        "'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)'"
        "],start_new_session=True); "
        "print(p.pid,flush=True); time.sleep(30)"
    )
    worker = scripted_worker(monkeypatch, script, graceful_termination_seconds=0.05)
    result = await worker.run(record(), tmp_path, discard, 0.3)
    assert result.status is WorkerStatus.TIMED_OUT
    assert worker.process.returncode == -signal.SIGKILL
    with pytest.raises(ProcessLookupError):
        os.killpg(worker.process.pid, 0)
    child = int(result.stdout.strip())
    # Linux can retain an orphan as a zombie until init reaps it. Verify the
    # child cannot keep working without requiring immediate PID disappearance.
    probe = await asyncio.create_subprocess_exec(
        "ps", "-p", str(child), "-o", "stat=", stdout=asyncio.subprocess.PIPE,
    )
    state, _ = await probe.communicate()
    assert not state.strip() or state.strip().startswith(b"Z")


@pytest.mark.asyncio
async def test_emit_failure_cleans_up_subprocess(monkeypatch, tmp_path):
    worker = scripted_worker(monkeypatch, "import time; print('ready', flush=True); time.sleep(30)")

    async def emit(*args):
        raise RuntimeError("observer failed")

    rec = record()
    with pytest.raises(RuntimeError, match="observer failed"):
        await worker.run(rec, tmp_path, emit, 10)
    assert rec.status is WorkerStatus.FAILED
    assert worker.process.returncode is not None

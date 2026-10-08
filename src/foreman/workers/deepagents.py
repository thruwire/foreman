from __future__ import annotations

import asyncio
import codecs
import math
import os
import signal
from datetime import UTC, datetime
from pathlib import Path

from foreman.models import EventType, WorkerRecord, WorkerStatus
from foreman.workers.base import EventCallback, worker_environment


class DeepAgentsWorker:
    """Run the separately installed Deep Agents Code CLI (deepagents-code >=0.1.83).

    Headless dcode streams text rather than a machine event stream. Read bounded
    chunks so output without newlines still reaches supervision. Native hooks
    are a separate attached-session integration; this transport has no live
    steering channel and uses Foreman's stop/retry policy.
    """

    supports_steering = False

    def __init__(
        self,
        *,
        executable: str = "dcode",
        model: str | None = None,
        max_turns: int = 200,
        shell_allow_list: str = "recommended",
        output_limit: int = 50_000,
        graceful_termination_seconds: float = 5.0,
    ) -> None:
        self.executable = executable
        self.model = model
        self.max_turns = max_turns
        self.shell_allow_list = shell_allow_list
        self.output_limit = output_limit
        self.graceful_termination_seconds = graceful_termination_seconds
        self.process: asyncio.subprocess.Process | None = None
        self._termination_reason: str | None = None
        self._process_groups: set[int] = set()

    def command(self, mission: str, timeout_seconds: float) -> list[str]:
        command = [
            self.executable,
            "--max-turns", str(self.max_turns),
            "--timeout", str(max(1, math.ceil(timeout_seconds))),
            "--shell-allow-list", self.shell_allow_list,
        ]
        if self.model is not None:
            command.extend(["--model", self.model])
        command.extend(["--non-interactive", mission])
        return command

    async def _stream(
        self,
        stream: asyncio.StreamReader | None,
        stream_name: str,
        record: WorkerRecord,
        emit: EventCallback,
    ) -> None:
        if stream is None:
            return
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            chunk = await stream.read(4_096)
            text = decoder.decode(chunk, final=not chunk)
            if text:
                current = getattr(record, stream_name)
                setattr(record, stream_name, (current + text)[-self.output_limit:])
                await emit(EventType.WORKER_OUTPUT, {
                    "worker_id": record.worker_id,
                    "stream": stream_name,
                    "line": text.rstrip()[-4_000:],
                })
            if not chunk:
                break

    async def run(
        self,
        record: WorkerRecord,
        repository: Path,
        emit: EventCallback,
        timeout_seconds: float,
    ) -> WorkerRecord:
        self._termination_reason = None
        self.process = None
        self._process_groups.clear()
        record.client = "deepagents"
        record.supports_steering = False
        record.status = WorkerStatus.RUNNING
        record.started_at = datetime.now(UTC)
        environment = worker_environment()
        environment["PYTHONUNBUFFERED"] = "1"
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command(record.mission, timeout_seconds),
                cwd=repository,
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except Exception as error:
            record.status = WorkerStatus.FAILED
            record.finished_at = datetime.now(UTC)
            record.stderr = (
                f"failed to launch Deep Agents Code: {error}. Install deepagents-code>=0.1.83 "
                "with Python 3.12+ and ensure dcode is on PATH, or set "
                "FOREMAN_DEEPAGENTS_EXECUTABLE."
            )[-self.output_limit:]
            record.termination_reason = "launch_failed"
            return record

        streams = [
            asyncio.create_task(self._stream(self.process.stdout, "stdout", record, emit)),
            asyncio.create_task(self._stream(self.process.stderr, "stderr", record, emit)),
        ]
        try:
            # Include pipe draining in the deadline: descendants can keep pipes open
            # after the CLI exits, and emit can trigger supervisory intervention.
            async def wait_and_drain() -> None:
                await asyncio.gather(self.process.wait(), *streams)

            await asyncio.wait_for(wait_and_drain(), timeout=timeout_seconds)
            if self._termination_reason:
                record.status = WorkerStatus.STOPPED
                record.termination_reason = self._termination_reason
            elif self.process.returncode == 124:
                record.status = WorkerStatus.TIMED_OUT
                record.termination_reason = "deepagents_budget_exhausted"
            elif self.process.returncode == 0:
                # A CLI exit is a worker result; Foreman still verifies completion.
                record.status = WorkerStatus.COMPLETED
            else:
                record.status = WorkerStatus.FAILED
                record.termination_reason = "nonzero_exit"
        except TimeoutError:
            record.status = WorkerStatus.TIMED_OUT
            record.termination_reason = "worker_timeout"
            await self.terminate("worker_timeout")
        except asyncio.CancelledError:
            record.status = WorkerStatus.CANCELLED
            record.termination_reason = "cancelled"
            await self.terminate("cancelled")
            raise
        except Exception:
            await self.terminate("stream_failed")
            record.status = WorkerStatus.FAILED
            record.termination_reason = "stream_failed"
            raise
        finally:
            for task in streams:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*streams, return_exceptions=True)
            record.exit_code = self.process.returncode
            record.finished_at = datetime.now(UTC)
        return record

    def _signal_group(self, sig: signal.Signals) -> None:
        process = self.process
        if process is None:
            return
        for group in self._process_groups | {process.pid}:
            try:
                os.killpg(group, sig)
            except (ProcessLookupError, PermissionError):
                if group == process.pid and process.returncode is None:
                    if sig == signal.SIGKILL:
                        process.kill()
                    else:
                        process.terminate()

    async def _collect_process_groups(self) -> None:
        # dcode launches its LangGraph server in a separate POSIX session.
        # Collect descendant groups before signaling the CLI, while ancestry
        # is still available; killing only the CLI's group would miss the server.
        process = self.process
        if process is None:
            return
        probe = None
        try:
            probe = await asyncio.create_subprocess_exec(
                "ps", "-e", "-o", "pid=", "-o", "ppid=",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env=worker_environment(),
            )
            output, _ = await asyncio.wait_for(probe.communicate(), timeout=2)
            children: dict[int, list[int]] = {}
            for line in output.decode().splitlines():
                pid, parent = map(int, line.split())
                children.setdefault(parent, []).append(pid)
            pending = [process.pid]
            own_group = os.getpgrp()
            while pending:
                pid = pending.pop()
                pending.extend(children.get(pid, ()))
                try:
                    group = os.getpgid(pid)
                except ProcessLookupError:
                    continue
                if group != own_group:
                    self._process_groups.add(group)
        except (OSError, ValueError, TimeoutError):
            # Minimal systems may lack ps; the CLI's own shutdown still owns
            # server cleanup, and its original group remains terminable.
            pass
        finally:
            if probe is not None and probe.returncode is None:
                probe.kill()
                await probe.wait()

    async def terminate(self, reason: str) -> None:
        self._termination_reason = reason
        process = self.process
        if process is None:
            return
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(process.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
            await process.wait()
            return
        await self._collect_process_groups()
        self._signal_group(signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=self.graceful_termination_seconds)
        except TimeoutError:
            self._signal_group(signal.SIGKILL)
            await process.wait()
        # The CLI may exit before its server or shell child does. Kill any
        # remaining members of this worker's process group as well.
        self._signal_group(signal.SIGKILL)

    async def steer(self, message: str) -> bool:
        del message
        return False

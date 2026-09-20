from __future__ import annotations

import asyncio
import os
import signal
from datetime import UTC, datetime
from pathlib import Path

from foreman.models import EventType, WorkerRecord, WorkerStatus
from foreman.workers.base import EventCallback, worker_environment


class OpenCodeWorker:
    """A worker using the installed OpenCode CLI in non-interactive mode.

    Runs ``opencode run`` as a streaming subprocess. The prompt is passed
    positionally (``-p`` is not the prompt flag in current OpenCode
    releases) and ``--auto`` keeps the headless run from stalling on
    permission prompts; fine-grained permissions remain governed by the
    user's opencode.json. There is no live-turn input channel, so
    ``steer`` always returns ``False`` and the policy falls back to
    stop/retry for this backend.
    """

    def __init__(
        self,
        *,
        executable: str = "opencode",
        model: str | None = None,
        output_limit: int = 50_000,
        graceful_termination_seconds: float = 5.0,
    ) -> None:
        self.executable = executable
        self.model = model
        self.output_limit = output_limit
        self.graceful_termination_seconds = graceful_termination_seconds
        self.process: asyncio.subprocess.Process | None = None
        self._termination_reason: str | None = None

    def command(self, mission: str) -> list[str]:
        command = [self.executable, "run", "--auto"]
        if self.model is not None:
            command.extend(["--model", self.model])
        command.append(mission)
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
        while line := await stream.readline():
            text = line.decode("utf-8", errors="replace")
            current = getattr(record, stream_name)
            setattr(record, stream_name, (current + text)[-self.output_limit :])
            await emit(
                EventType.WORKER_OUTPUT,
                {
                    "worker_id": record.worker_id,
                    "stream": stream_name,
                    "line": text.rstrip()[-4_000:],
                },
            )

    async def run(
        self,
        record: WorkerRecord,
        repository: Path,
        emit: EventCallback,
        timeout_seconds: float,
    ) -> WorkerRecord:
        record.status = WorkerStatus.RUNNING
        record.started_at = datetime.now(UTC)
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command(record.mission),
                cwd=repository,
                env=worker_environment(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except Exception as error:
            record.status = WorkerStatus.FAILED
            record.finished_at = datetime.now(UTC)
            record.stderr = f"failed to launch OpenCode: {error}"
            record.termination_reason = "launch_failed"
            return record

        # Drain both pipes concurrently to avoid deadlocks and expose activity before process exit.
        stdout_task = asyncio.create_task(self._stream(self.process.stdout, "stdout", record, emit))
        stderr_task = asyncio.create_task(self._stream(self.process.stderr, "stderr", record, emit))
        try:
            await asyncio.wait_for(self.process.wait(), timeout=timeout_seconds)
            record.exit_code = self.process.returncode
            if self._termination_reason:
                record.status = WorkerStatus.STOPPED
                record.termination_reason = self._termination_reason
            elif self.process.returncode == 0:
                record.status = WorkerStatus.COMPLETED
            else:
                record.status = WorkerStatus.FAILED
                record.termination_reason = "nonzero_exit"
        except TimeoutError:
            record.status = WorkerStatus.TIMED_OUT
            record.termination_reason = "worker_timeout"
            await self.terminate("worker_timeout")
            record.exit_code = self.process.returncode
        except asyncio.CancelledError:
            record.status = WorkerStatus.CANCELLED
            record.termination_reason = "cancelled"
            await self.terminate("cancelled")
            raise
        finally:
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            record.finished_at = datetime.now(UTC)
        return record

    async def terminate(self, reason: str) -> None:
        self._termination_reason = reason
        process = self.process
        if process is None or process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=self.graceful_termination_seconds)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
            await process.wait()

    async def steer(self, message: str) -> bool:
        # Non-interactive `opencode run` has no active-turn input channel.
        del message
        return False

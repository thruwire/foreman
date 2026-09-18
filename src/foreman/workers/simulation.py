from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from foreman.models import EventType, WorkerRecord, WorkerStatus
from foreman.workers.base import EventCallback


class FakeWorker:
    """A deterministic simulated worker with real cancellation/termination semantics."""

    def __init__(
        self,
        *,
        output_lines: list[str] | None = None,
        delay_seconds: float = 0.05,
        exit_code: int = 0,
        stderr_lines: list[str] | None = None,
        wait_forever: bool = False,
    ) -> None:
        self.output_lines = output_lines or ["Inspecting repository", "Editing files", "Tests pass"]
        self.stderr_lines = stderr_lines or []
        self.delay_seconds = delay_seconds
        self.exit_code = exit_code
        self.wait_forever = wait_forever
        self._stop = asyncio.Event()
        self._termination_reason: str | None = None
        self.steering_messages: list[str] = []

    async def run(
        self,
        record: WorkerRecord,
        repository: Path,
        emit: EventCallback,
        timeout_seconds: float,
    ) -> WorkerRecord:
        del repository
        record.status = WorkerStatus.RUNNING
        record.started_at = datetime.now(UTC)

        async def work() -> None:
            for line in self.output_lines:
                if self._stop.is_set():
                    break
                await asyncio.sleep(self.delay_seconds)
                record.stdout += f"{line}\n"
                await emit(
                    EventType.WORKER_OUTPUT,
                    {"worker_id": record.worker_id, "stream": "stdout", "line": line},
                )
            for line in self.stderr_lines:
                if self._stop.is_set():
                    break
                await asyncio.sleep(self.delay_seconds)
                record.stderr += f"{line}\n"
                await emit(
                    EventType.WORKER_OUTPUT,
                    {"worker_id": record.worker_id, "stream": "stderr", "line": line},
                )
            if self.wait_forever and not self._stop.is_set():
                await self._stop.wait()

        try:
            await asyncio.wait_for(work(), timeout=timeout_seconds)
            record.exit_code = self.exit_code
            if self._stop.is_set():
                record.status = WorkerStatus.STOPPED
                record.termination_reason = self._termination_reason or "stopped"
            elif self.exit_code == 0:
                record.status = WorkerStatus.COMPLETED
            else:
                record.status = WorkerStatus.FAILED
                record.termination_reason = "nonzero_exit"
        except TimeoutError:
            record.status = WorkerStatus.TIMED_OUT
            record.termination_reason = "worker_timeout"
            record.exit_code = -1
        except asyncio.CancelledError:
            record.status = WorkerStatus.CANCELLED
            record.termination_reason = "cancelled"
            record.finished_at = datetime.now(UTC)
            raise
        record.finished_at = datetime.now(UTC)
        return record

    async def terminate(self, reason: str) -> None:
        self._termination_reason = reason
        self._stop.set()

    async def steer(self, message: str) -> bool:
        if self._stop.is_set():
            return False
        self.steering_messages.append(message)
        return True

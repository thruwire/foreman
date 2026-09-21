from __future__ import annotations

import asyncio
import json
import os
import signal
from datetime import UTC, datetime
from pathlib import Path

from foreman.models import EventType, WorkerRecord, WorkerStatus
from foreman.workers.base import EventCallback, worker_environment


class HermesWorker:
    """A worker driving the local Hermes Agent CLI in headless single-query mode.

    Runs ``hermes chat -q <mission> -Q --format stream-json --in <repo>`` as a
    streaming subprocess. Hermes emits newline-delimited JSON events (system
    init, tool_use, tool_result, text); each line is decoded, tail-bounded into
    the worker record, and forwarded to the factory event stream so the
    supervisor sees tool activity, not just final text.

    There is no live-turn input channel for a headless single query, so
    ``steer`` always returns ``False`` and the policy falls back to stop/retry
    for this backend. Termination mirrors the OpenCode backend: SIGTERM to the
    process group, then SIGKILL after the graceful window.
    """

    supports_steering = False

    def __init__(
        self,
        *,
        executable: str = "hermes",
        model: str | None = None,
        provider: str | None = None,
        max_turns: int = 200,
        toolsets: str | None = None,
        extra_args: list[str] | None = None,
        output_limit: int = 50_000,
        graceful_termination_seconds: float = 5.0,
    ) -> None:
        self.executable = executable
        self.model = model
        self.provider = provider
        self.max_turns = max_turns
        self.toolsets = toolsets
        self.extra_args = extra_args or []
        self.output_limit = output_limit
        self.graceful_termination_seconds = graceful_termination_seconds
        self.process: asyncio.subprocess.Process | None = None
        self._termination_reason: str | None = None

    def command(self, mission: str, repository: Path) -> list[str]:
        command = [
            self.executable,
            "chat",
            "--in",
            str(repository),
            "-q",
            mission,
            "-Q",
            "--format",
            "stream-json",
            "--max-turns",
            str(self.max_turns),
        ]
        if self.model is not None:
            command.extend(["-m", self.model])
        if self.provider is not None:
            command.extend(["--provider", self.provider])
        if self.toolsets is not None:
            command.extend(["-t", self.toolsets])
        command.extend(self.extra_args)
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
                self._event_payload(text, stream_name, record),
            )

    def _event_payload(
        self, text: str, stream_name: str, record: WorkerRecord
    ) -> dict[str, object]:
        """Build the bounded worker-output payload for one NDJSON line.

        Structured Hermes events (tool_use / tool_result / system) get compact
        summaries in ``line`` plus typed extras; anything unparseable passes
        through as raw text. Payloads stay bounded like the other backends.
        """
        payload: dict[str, object] = {
            "worker_id": record.worker_id,
            "stream": stream_name,
            "line": text.rstrip()[-4_000:],
        }
        stripped = text.strip()
        if stripped.startswith("{") and stream_name == "stdout":
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                event = None
            if isinstance(event, dict):
                kind = event.get("type")
                if kind == "tool_use":
                    name = str(event.get("name", "tool"))
                    try:
                        tool_input = json.dumps(event.get("input", {}))
                    except (TypeError, ValueError):
                        tool_input = str(event.get("input", ""))[:600]
                    payload["line"] = f"[tool_use] {name} {tool_input[:600]}"
                    payload["kind"] = "tool_use"
                    payload["tool"] = name
                elif kind == "tool_result":
                    name = str(event.get("name", "tool"))
                    is_error = bool(event.get("is_error", False))
                    output = str(event.get("output", ""))[-600:]
                    flag = " (error)" if is_error else ""
                    payload["line"] = f"[tool_result] {name}{flag}: {output}"
                    payload["kind"] = "tool_result"
                    payload["tool"] = name
                elif kind == "system":
                    payload["line"] = (
                        f"[session] model={event.get('model', '?')} "
                        f"session={event.get('session_id', '?')}"
                    )
                    payload["kind"] = "system"
        return payload

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
                *self.command(record.mission, repository),
                cwd=repository,
                env=worker_environment(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except Exception as error:
            record.status = WorkerStatus.FAILED
            record.finished_at = datetime.now(UTC)
            record.stderr = f"failed to launch Hermes: {error}"
            record.termination_reason = "launch_failed"
            return record

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
        # Headless single-query mode has no active-turn input channel.
        del message
        return False

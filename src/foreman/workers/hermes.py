from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from foreman.models import EventType, WorkerRecord, WorkerStatus
from foreman.workers.base import EventCallback, worker_environment

# Per-line cap for the worker's stdout/stderr readers (asyncio default: 64 KiB).
STREAM_LINE_LIMIT = 16 * 1024 * 1024


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
        probe_capabilities: bool = True,
    ) -> None:
        self.executable = executable
        self.model = model
        self.provider = provider
        self.max_turns = max_turns
        self.toolsets = toolsets
        self.extra_args = extra_args or []
        self.output_limit = output_limit
        self.graceful_termination_seconds = graceful_termination_seconds
        self.probe_capabilities = probe_capabilities
        self._caps: dict[str, bool] | None = None
        self.process: asyncio.subprocess.Process | None = None
        self._termination_reason: str | None = None
        # exit_code from hermes' own NDJSON `result` event, when one arrived
        self._result_exit_code: object = None

    def _probe_capabilities(self) -> dict[str, bool]:
        """Detect which chat flags this hermes build supports (runs once).

        Older Hermes releases lack ``--format stream-json`` and/or ``--in``.
        Missing stream-json degrades observation to raw stdout text; missing
        ``--in`` is safe because the subprocess cwd is the repository anyway.
        """
        if self._caps is not None:
            return self._caps
        if not self.probe_capabilities:
            self._caps = {"format_stream_json": True, "in_flag": True}
            return self._caps
        caps = {"format_stream_json": False, "in_flag": False}
        try:
            r = subprocess.run(
                [self.executable, "chat", "--help"],
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
            help_text = (r.stdout or "") + (r.stderr or "")
            caps["format_stream_json"] = "stream-json" in help_text
            caps["in_flag"] = "--in" in help_text
        except Exception:
            # Probe failure: assume the modern flag set; launch errors surface
            # in run() as before.
            caps = {"format_stream_json": True, "in_flag": True}
        self._caps = caps
        return caps

    def command(self, mission: str, repository: Path) -> list[str]:
        caps = self._probe_capabilities()
        command = [self.executable, "chat"]
        if caps["in_flag"]:
            command.extend(["--in", str(repository)])
        command.extend(["-q", mission, "-Q"])
        if caps["format_stream_json"]:
            command.extend(["--format", "stream-json"])
        command.extend(["--max-turns", str(self.max_turns)])
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
        while True:
            try:
                line = await stream.readline()
            except ValueError:
                # One NDJSON line (a big tool_result) overran the reader limit.
                # readline() has already discarded the buffered part; keep
                # reading, otherwise every later event is lost and the full
                # pipe eventually blocks hermes itself.
                line = b"[oversized output line truncated]\n"
            if not line:
                break
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
                elif kind == "result":
                    payload["kind"] = "result"
                    self._result_exit_code = event.get("exit_code")
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
        # Some hermes builds resolve the terminal cwd from config/env rather
        # than the process cwd (or accept --in but still seed the shell from
        # terminal.cwd). Pin the workspace in the mission itself so the agent
        # cds first no matter where the tool layer starts the shell.
        repo_path = str(repository)
        record.mission = (
            f"[Working directory: {repo_path}. Run every shell command from "
            f'this directory (prefix with `cd "{repo_path}"` or set it '
            f"explicitly); all repository work, tests, and git commands "
            f"target this path. Do NOT commit or push: leave all changes "
            f"uncommitted in the working tree — the supervisor reads the "
            f"evidence from the git diff.]\n\n{record.mission}"
        )
        try:
            env = worker_environment()
            # Hermes NDJSON events must arrive line-by-line for the supervisor
            # to see progress; Python block-buffers stdout when piped, so a
            # long-running worker would stream nothing until exit (Jev then
            # sees an 'empty' factory and kills healthy workers).
            env["PYTHONUNBUFFERED"] = "1"
            self.process = await asyncio.create_subprocess_exec(
                *self.command(record.mission, repository),
                cwd=repository,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                # hermes emits each tool_result as one NDJSON line; the 64 KiB
                # default readline limit is routinely exceeded on real repos.
                limit=STREAM_LINE_LIMIT,
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
            elif self._result_exit_code == 0:
                # hermes reported a successful turn, then its post-turn
                # housekeeping (memory review, cleanup) exited nonzero.
                record.status = WorkerStatus.COMPLETED
                record.stderr = (
                    f"{record.stderr}\n[foreman: hermes exited {self.process.returncode} "
                    f"after a successful result event]"
                )[-self.output_limit :]
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
            results = await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            for stream_name, outcome in zip(("stdout", "stderr"), results, strict=True):
                if isinstance(outcome, BaseException) and not isinstance(
                    outcome, asyncio.CancelledError
                ):
                    # A dead reader silently blinds the supervisor; leave a trace.
                    record.stderr = (
                        f"{record.stderr}\n[foreman: {stream_name} reader failed: "
                        f"{type(outcome).__name__}: {outcome}]"
                    )[-self.output_limit :]
            record.finished_at = datetime.now(UTC)
        return record

    async def terminate(self, reason: str) -> None:
        self._termination_reason = reason
        process = self.process
        if process is None or process.returncode is not None:
            return
        if os.name == "nt":
            # Windows has no process groups / killpg; taskkill /T reaches the
            # whole tree (hermes.exe spawns node children), /F forces it.
            # asyncio.to_thread keeps the async loop unblocked (ASYNC221).
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=15,
                check=False,
            )
            await process.wait()
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

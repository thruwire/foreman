from __future__ import annotations

import asyncio
import json
import os
import signal
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from foreman.models import EventType, WorkerRecord, WorkerStatus
from foreman.version import __version__
from foreman.workers.base import EventCallback, codex_environment


class CodexAppServerError(RuntimeError):
    """Raised when the Codex App Server transport cannot complete a request."""


class CodexAppServerWorker:
    """A steerable Codex worker backed by the App Server JSON-RPC protocol."""

    supports_steering = True

    def __init__(
        self,
        *,
        executable: str = "codex",
        sandbox: str = "workspace-write",
        output_limit: int = 50_000,
        graceful_termination_seconds: float = 5.0,
        request_timeout_seconds: float = 10.0,
    ) -> None:
        self.executable = executable
        self.sandbox = sandbox
        self.output_limit = output_limit
        self.graceful_termination_seconds = graceful_termination_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.process: asyncio.subprocess.Process | None = None
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self._record: WorkerRecord | None = None
        self._emit: EventCallback | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_request_id = 0
        self._write_lock = asyncio.Lock()
        self._turn_started = asyncio.Event()
        self._turn_completed = asyncio.Event()
        self._completed_turn: dict[str, Any] | None = None
        self._reader_error: Exception | None = None
        self._termination_reason: str | None = None
        self._delta_buffers: dict[tuple[str, str], str] = {}
        self._streamed_item_ids: set[str] = set()

    def command(self) -> list[str]:
        return [self.executable, "app-server", "--listen", "stdio://"]

    async def _write(self, message: Mapping[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.returncode is not None:
            raise CodexAppServerError("Codex App Server is not running")
        payload = f"{json.dumps(dict(message), separators=(',', ':'))}\n".encode()
        async with self._write_lock:
            process.stdin.write(payload)
            await process.stdin.drain()

    async def _request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        request_timeout: float | None = None,
    ) -> dict[str, Any]:
        self._next_request_id += 1
        request_id = self._next_request_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({"method": method, "id": request_id, "params": dict(params)})
            return await asyncio.wait_for(
                asyncio.shield(future),
                timeout=request_timeout or self.request_timeout_seconds,
            )
        except TimeoutError as error:
            raise CodexAppServerError(f"Codex App Server request timed out: {method}") from error
        finally:
            self._pending.pop(request_id, None)

    async def _notify(self, method: str, params: Mapping[str, Any]) -> None:
        await self._write({"method": method, "params": dict(params)})

    async def _emit_output(self, stream: str, text: str) -> None:
        record = self._record
        emit = self._emit
        if record is None or emit is None or not text:
            return
        current = getattr(record, stream)
        setattr(record, stream, (current + text)[-self.output_limit :])
        await emit(
            EventType.WORKER_OUTPUT,
            {
                "worker_id": record.worker_id,
                "stream": stream,
                "line": text.rstrip()[-4_000:],
            },
        )

    async def _buffer_output(self, item_id: str, stream: str, text: str) -> None:
        key = (item_id, stream)
        buffer = self._delta_buffers.get(key, "") + text
        self._streamed_item_ids.add(item_id)
        while "\n" in buffer or len(buffer) >= 1_000:
            newline = buffer.find("\n")
            split_at = newline + 1 if 0 <= newline < 1_000 else min(1_000, len(buffer))
            await self._emit_output(stream, buffer[:split_at])
            buffer = buffer[split_at:]
        self._delta_buffers[key] = buffer

    async def _flush_item_output(self, item_id: str | None = None) -> None:
        keys = [
            key for key in self._delta_buffers if item_id is None or key[0] == item_id
        ]
        for key in keys:
            text = self._delta_buffers.pop(key)
            if text:
                await self._emit_output(key[1], text)

    async def _handle_notification(self, message: Mapping[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str) or not isinstance(params, Mapping):
            return

        notification_thread = params.get("threadId")
        if self.thread_id is not None and notification_thread not in {None, self.thread_id}:
            return

        if method in {"turn/started", "turn/completed"}:
            turn = params.get("turn")
            turn_id = turn.get("id") if isinstance(turn, Mapping) else None
            if isinstance(turn_id, str) and self.turn_id is None:
                self.turn_id = turn_id
                self._turn_started.set()
                if self._record is not None:
                    self._record.codex_turn_id = turn_id
            if method == "turn/completed" and isinstance(turn, Mapping) and turn_id == self.turn_id:
                await self._flush_item_output()
                self._completed_turn = dict(turn)
                self._turn_completed.set()
            return

        delta_streams = {
            "item/agentMessage/delta": "stdout",
            "item/plan/delta": "stdout",
            "item/commandExecution/outputDelta": "stdout",
            "item/fileChange/outputDelta": "stdout",
            "item/reasoning/summaryTextDelta": "stdout",
        }
        if method in delta_streams:
            item_id = params.get("itemId")
            delta = params.get("delta")
            if isinstance(item_id, str) and isinstance(delta, str):
                await self._buffer_output(item_id, delta_streams[method], delta)
            return

        if method in {"item/started", "item/completed"}:
            item = params.get("item")
            if isinstance(item, Mapping):
                item_type = str(item.get("type", "item"))
                item_id = item.get("id")
                if method == "item/started":
                    text = f"{item_type} started\n"
                elif isinstance(item_id, str) and item_id in self._streamed_item_ids:
                    await self._flush_item_output(item_id)
                    text = f"{item_type} completed\n"
                else:
                    text = f"{json.dumps(dict(item), ensure_ascii=False, default=str)}\n"
                await self._emit_output("stdout", text)
            return

        if method == "error":
            error = params.get("error", params)
            await self._emit_output(
                "stderr", f"Codex App Server error: {json.dumps(error, default=str)}\n"
            )

    async def _reader_loop(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        try:
            while line := await process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    await self._emit_output(
                        "stderr",
                        f"Malformed Codex App Server response: {line.decode(errors='replace')}",
                    )
                    continue
                if not isinstance(message, Mapping):
                    continue
                request_id = message.get("id")
                if isinstance(request_id, int) and request_id in self._pending:
                    future = self._pending[request_id]
                    if "error" in message:
                        error = message["error"]
                        future.set_exception(
                            CodexAppServerError(
                                f"Codex App Server request failed: {json.dumps(error, default=str)}"
                            )
                        )
                    else:
                        result = message.get("result", {})
                        future.set_result(dict(result) if isinstance(result, Mapping) else {})
                    continue
                await self._handle_notification(message)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._reader_error = error
        finally:
            self._turn_started.set()
            if not self._turn_completed.is_set():
                self._reader_error = self._reader_error or CodexAppServerError(
                    "Codex App Server closed before the turn completed"
                )
                self._turn_completed.set()
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(self._reader_error or CodexAppServerError("server closed"))

    async def _stderr_loop(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        while line := await process.stderr.readline():
            await self._emit_output("stderr", line.decode("utf-8", errors="replace"))

    async def _start_server(self, repository: Path) -> None:
        self.process = await asyncio.create_subprocess_exec(
            *self.command(),
            cwd=repository,
            env=codex_environment(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())
        await self._request(
            "initialize",
            {
                "clientInfo": {
                    "name": "foreman",
                    "title": "Foreman",
                    "version": __version__,
                }
            },
        )
        await self._notify("initialized", {})

    async def _interrupt(self) -> None:
        if self.thread_id is None or self.turn_id is None or self._turn_completed.is_set():
            return
        await self._request(
            "turn/interrupt",
            {"threadId": self.thread_id, "turnId": self.turn_id},
            request_timeout=max(0.1, self.graceful_termination_seconds),
        )

    async def _close_server(self) -> None:
        process = self.process
        if process is not None and process.returncode is None:
            if process.stdin is not None:
                process.stdin.close()
            try:
                await asyncio.wait_for(
                    process.wait(), timeout=max(0.1, self.graceful_termination_seconds)
                )
            except TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    process.kill()
                await process.wait()
        tasks = [task for task in (self._reader_task, self._stderr_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run(
        self,
        record: WorkerRecord,
        repository: Path,
        emit: EventCallback,
        timeout_seconds: float,
    ) -> WorkerRecord:
        self._record = record
        self._emit = emit
        record.status = WorkerStatus.RUNNING
        record.started_at = datetime.now(UTC)
        try:
            await self._start_server(repository)
            thread_response = await self._request(
                "thread/start",
                {
                    "cwd": str(repository),
                    "approvalPolicy": "never",
                    "sandbox": self.sandbox,
                    "serviceName": "foreman",
                },
            )
            thread = thread_response.get("thread")
            if not isinstance(thread, Mapping) or not isinstance(thread.get("id"), str):
                raise CodexAppServerError("thread/start response omitted the thread id")
            self.thread_id = thread["id"]
            record.codex_thread_id = self.thread_id

            turn_response = await self._request(
                "turn/start",
                {
                    "threadId": self.thread_id,
                    "input": [{"type": "text", "text": record.mission}],
                },
            )
            turn = turn_response.get("turn")
            if not isinstance(turn, Mapping) or not isinstance(turn.get("id"), str):
                raise CodexAppServerError("turn/start response omitted the turn id")
            self.turn_id = turn["id"]
            self._turn_started.set()
            record.codex_turn_id = self.turn_id

            await asyncio.wait_for(self._turn_completed.wait(), timeout=timeout_seconds)
            if self._reader_error is not None:
                raise self._reader_error
            status = str((self._completed_turn or {}).get("status", "failed"))
            error = (self._completed_turn or {}).get("error")
            if self._termination_reason is not None or status == "interrupted":
                record.status = WorkerStatus.STOPPED
                record.termination_reason = self._termination_reason or "interrupted"
                record.exit_code = -15
            elif status == "completed":
                record.status = WorkerStatus.COMPLETED
                record.exit_code = 0
            else:
                record.status = WorkerStatus.FAILED
                record.termination_reason = "codex_turn_failed"
                record.exit_code = 1
                if error:
                    await self._emit_output(
                        "stderr", f"Codex turn failed: {json.dumps(error, default=str)}\n"
                    )
        except TimeoutError:
            record.status = WorkerStatus.TIMED_OUT
            record.termination_reason = "worker_timeout"
            record.exit_code = -1
            try:
                await self._interrupt()
            except CodexAppServerError:
                pass
        except asyncio.CancelledError:
            record.status = WorkerStatus.CANCELLED
            record.termination_reason = "cancelled"
            try:
                await self._interrupt()
            except CodexAppServerError:
                pass
            raise
        except Exception as error:
            if self._termination_reason is not None:
                record.status = WorkerStatus.STOPPED
                record.termination_reason = self._termination_reason
                record.exit_code = -15
            else:
                record.status = WorkerStatus.FAILED
                record.termination_reason = "app_server_error"
                record.exit_code = 1
                await self._emit_output("stderr", f"{type(error).__name__}: {error}\n")
        finally:
            record.finished_at = datetime.now(UTC)
            await self._close_server()
        return record

    async def steer(self, message: str) -> bool:
        if self.thread_id is None or self.turn_id is None:
            try:
                await asyncio.wait_for(
                    self._turn_started.wait(), timeout=self.request_timeout_seconds
                )
            except TimeoutError:
                return False
        if self.thread_id is None or self.turn_id is None or self._turn_completed.is_set():
            return False
        try:
            result = await self._request(
                "turn/steer",
                {
                    "threadId": self.thread_id,
                    "expectedTurnId": self.turn_id,
                    "input": [{"type": "text", "text": message}],
                },
            )
        except CodexAppServerError:
            return False
        return result.get("turnId") == self.turn_id

    async def terminate(self, reason: str) -> None:
        self._termination_reason = reason
        if self.thread_id is None or self.turn_id is None:
            process = self.process
            if process is not None and process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    process.terminate()
            self._completed_turn = {"id": self.turn_id, "status": "interrupted"}
            self._turn_completed.set()
            return
        try:
            await self._interrupt()
            await asyncio.wait_for(
                self._turn_completed.wait(),
                timeout=max(0.1, self.graceful_termination_seconds),
            )
        except (CodexAppServerError, TimeoutError):
            process = self.process
            if process is not None and process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    process.terminate()
            self._completed_turn = {"id": self.turn_id, "status": "interrupted"}
            self._turn_completed.set()

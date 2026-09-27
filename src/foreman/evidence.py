from __future__ import annotations

import asyncio
import os
import signal
import time
from collections import deque
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol

from foreman.config import CommandEvidenceConfig
from foreman.models import CommandEvidenceResult


class EvidenceSelection(Protocol):
    evidence: tuple[str, ...] | None


# Each built-in provider owns a coherent fragment of FactoryObservation. Checks
# that omit `evidence` receive this complete default list.
BUILTIN_EVIDENCE_FIELDS: dict[str, tuple[str, ...]] = {
    "worker": (
        "active_workers",
        "worker_history",
        "latest_worker_output",
        "worker_exit_status",
        "worker_elapsed_seconds",
    ),
    "git.status": ("git_status", "changed_files"),
    "git.diff": ("git_diff",),
    "git.untracked": ("untracked_evidence",),
    "repository.instructions": ("agents_md_path", "agents_md_instructions"),
    "tests": ("test_results",),
    "verification": ("verification_results",),
    "events": ("recent_events",),
    "history": ("previous_result", "previous_intervention", "failures"),
}
DEFAULT_EVIDENCE_PROVIDERS = tuple(BUILTIN_EVIDENCE_FIELDS)


def evidence_for(selection: EvidenceSelection) -> tuple[str, ...]:
    return selection.evidence or DEFAULT_EVIDENCE_PROVIDERS


def selected_command_evidence(
    selections: Iterable[EvidenceSelection],
    configured: Sequence[CommandEvidenceConfig],
) -> tuple[CommandEvidenceConfig, ...]:
    requested = {
        provider
        for selection in selections
        for provider in evidence_for(selection)
        if provider.startswith("command.")
    }
    return tuple(
        provider for provider in configured if f"command.{provider.id}" in requested
    )


def unknown_evidence_providers(
    selections: Iterable[EvidenceSelection],
    configured: Sequence[CommandEvidenceConfig],
) -> set[str]:
    known = {*BUILTIN_EVIDENCE_FIELDS, *(f"command.{item.id}" for item in configured)}
    return {
        provider
        for selection in selections
        for provider in evidence_for(selection)
        if provider not in known
    }


class _TailBuffer:
    """Drain a subprocess stream while retaining only a bounded byte tail."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.parts: deque[bytes] = deque()
        self.size = 0
        self.discarded = 0

    def append(self, chunk: bytes) -> None:
        if len(chunk) >= self.limit:
            self.discarded += self.size + len(chunk) - self.limit
            self.parts.clear()
            self.parts.append(chunk[-self.limit :])
            self.size = self.limit
            return
        self.parts.append(chunk)
        self.size += len(chunk)
        while self.size > self.limit and self.parts:
            excess = self.size - self.limit
            first = self.parts[0]
            if len(first) <= excess:
                self.parts.popleft()
                self.size -= len(first)
                self.discarded += len(first)
            else:
                self.parts[0] = first[excess:]
                self.size -= excess
                self.discarded += excess

    def text(self) -> str:
        body = b"".join(self.parts).decode("utf-8", errors="replace")
        if not self.discarded:
            return body
        return f"[... {self.discarded} earlier bytes omitted ...]\n{body}"


async def _drain(
    stream: asyncio.StreamReader | None,
    buffer: _TailBuffer,
) -> None:
    if stream is None:
        return
    while chunk := await stream.read(65_536):
        buffer.append(chunk)


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        except FileNotFoundError:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
            return
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    await process.wait()


async def run_command_evidence(
    provider: CommandEvidenceConfig,
    repository: Path,
    *,
    worker_id: str,
    output_limit: int,
) -> CommandEvidenceResult:
    """Collect one command provider directly, with no shell and bounded output."""

    started = time.monotonic()
    stdout = _TailBuffer(output_limit)
    stderr = _TailBuffer(output_limit)
    try:
        process = await asyncio.create_subprocess_exec(
            *provider.command,
            cwd=repository,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
        )
    except (OSError, ValueError) as error:
        return CommandEvidenceResult(
            provider_id=f"command.{provider.id}",
            worker_id=worker_id,
            status="launch_failed",
            stderr_tail=f"{type(error).__name__}: {error}",
            elapsed_seconds=max(0.0, time.monotonic() - started),
        )

    stdout_task = asyncio.create_task(_drain(process.stdout, stdout))
    stderr_task = asyncio.create_task(_drain(process.stderr, stderr))
    status = "completed"
    try:
        await asyncio.wait_for(process.wait(), timeout=provider.timeout_seconds)
    except TimeoutError:
        status = "timed_out"
        await _terminate_process(process)
    except asyncio.CancelledError:
        await _terminate_process(process)
        raise
    finally:
        await asyncio.gather(stdout_task, stderr_task)

    return CommandEvidenceResult(
        provider_id=f"command.{provider.id}",
        worker_id=worker_id,
        status=status,
        exit_code=process.returncode,
        stdout_tail=stdout.text(),
        stderr_tail=stderr.text(),
        elapsed_seconds=max(0.0, time.monotonic() - started),
    )


async def run_command_evidence_providers(
    providers: Sequence[CommandEvidenceConfig],
    repository: Path,
    *,
    worker_id: str,
    output_limit: int,
) -> list[CommandEvidenceResult]:
    """Collect commands sequentially so repository-mutating tools cannot race."""

    results = []
    for provider in providers:
        results.append(
            await run_command_evidence(
                provider,
                repository,
                worker_id=worker_id,
                output_limit=output_limit,
            )
        )
    return results

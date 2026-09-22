from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from foreman.config import FactoryConfig
from foreman.models import FactoryAssessment, FactoryState, Intervention, WorkerRecord
from foreman.persistence import RunStore


class FactoryObservation(BaseModel):
    """A compact, bounded snapshot of the evidence visible to Foreman."""

    model_config = ConfigDict(extra="forbid")

    original_job: str
    run_id: str
    factory_status: str
    iteration: int = Field(ge=0)
    active_workers: list[dict[str, Any]]
    worker_history: list[dict[str, Any]]
    latest_worker_output: str
    worker_exit_status: dict[str, int | None]
    worker_elapsed_seconds: dict[str, float]
    git_status: str
    git_diff: str
    untracked_evidence: str = ""
    changed_files: list[str]
    agents_md_path: str | None = None
    agents_md_instructions: str = ""
    test_results: list[dict[str, Any]]
    verification_results: list[dict[str, Any]]
    recent_events: list[dict[str, Any]]
    previous_assessment: FactoryAssessment | None
    previous_intervention: Intervention | None
    attempts: int = Field(ge=0)
    failures: list[str]
    elapsed_factory_seconds: float = Field(ge=0.0)

    @field_validator(
        "original_job",
        "latest_worker_output",
        "git_status",
        "git_diff",
        "agents_md_instructions",
        mode="before",
    )
    @classmethod
    def strings_only(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("observation text fields must be strings")
        return value


def _tail(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"[... {len(value) - limit} earlier characters omitted ...]\n{value[-limit:]}"


def _repository_agents_md(repository: Path, limit: int) -> tuple[str | None, str]:
    """Read the repository-root Codex instructions without retaining them in factory state."""

    for filename in ("AGENTS.override.md", "AGENTS.md"):
        path = repository / filename
        if path.is_symlink():
            continue
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                instructions = handle.read(limit + 1)
        except OSError:
            continue
        if not instructions.strip():
            continue
        if len(instructions) > limit:
            instructions = f"{instructions[:limit]}\n[... instructions truncated ...]"
        return filename, instructions
    return None, ""


def _bounded_worker(worker: WorkerRecord, output_limit: int) -> dict[str, Any]:
    return {
        "worker_id": worker.worker_id,
        "worker_type": worker.worker_type.value,
        "status": worker.status.value,
        "attempt": worker.attempt,
        "started_at": worker.started_at.isoformat() if worker.started_at else None,
        "finished_at": worker.finished_at.isoformat() if worker.finished_at else None,
        "elapsed_seconds": worker.duration_seconds,
        "exit_code": worker.exit_code,
        "stdout_tail": _tail(worker.stdout, output_limit),
        "stderr_tail": _tail(worker.stderr, output_limit),
        "termination_reason": worker.termination_reason,
        "codex_thread_id": worker.codex_thread_id,
        "codex_turn_id": worker.codex_turn_id,
        "supports_steering": worker.supports_steering,
        "steer_count": worker.steer_count,
        "last_steered_at": (worker.last_steered_at.isoformat() if worker.last_steered_at else None),
        "steering_history": worker.steering_history[-3:],
    }


async def _git(repository: Path, *args: str, limit: int) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(repository),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5.0)
    except (FileNotFoundError, TimeoutError):
        return ""
    output = stdout if process.returncode == 0 else stderr
    return _tail(output.decode("utf-8", errors="replace"), limit)


UNTRACKED_FILE_LIMIT = 6


def _untracked_evidence(repository: Path, git_status: str, limit: int) -> str:
    """Bounded head-of-content for untracked files named in git_status.

    hermes-style workers write test files as new (untracked) files; `git diff`
    never shows them, leaving the supervisor without test evidence. Include a
    bounded excerpt of each untracked text file (up to UNTRACKED_FILE_LIMIT
    files sharing `limit` chars) so assessments can see new tests/source.
    Binary-looking files are skipped. Read-only: the repository is never
    mutated. Expects `git status --untracked-files=all` output; a collapsed
    `?? dir/` entry carries no file names and is skipped.
    """
    paths: list[str] = []
    for line in git_status.splitlines():
        line = line.strip()
        if line.startswith("?? "):
            candidate = line[3:].strip().strip('"')
            if candidate and not candidate.endswith("/"):
                paths.append(candidate)
    if not paths:
        return ""

    selected = paths[:UNTRACKED_FILE_LIMIT]
    per_file = max(500, limit // len(selected))
    chunks: list[str] = []
    collected = 0
    for name in selected:
        path = repository / name
        try:
            if not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:4096]:
            continue  # binary
        text = raw.decode("utf-8", errors="replace")
        excerpt = text[:per_file]
        if len(text) > per_file:
            excerpt += f"\n... (truncated, {len(text)} chars total)"
        chunks.append(f"--- untracked: {name} ---\n{excerpt}")
        collected += len(excerpt)
        if collected >= limit:
            break
    return "\n".join(chunks)


_PYTEST_LINE = re.compile(
    r"^(?:=+\s*)?"
    r"(?P<parts>(?:(?:\d+\s+\w+)(?:,\s*)?)+)"
    r"\s+in\s+(?P<time>[\d.]+)s"
    r"(?:\s*=+)?\s*$"
)


def _pytest_summary_from_output(output: str) -> list[dict[str, Any]]:
    """Extract pytest summary lines from worker output into test_results
    entries so assessments can key on executed test evidence even when the
    backend streams no structured events (hermes buffers NDJSON on Windows).
    Handles 'N passed', 'N failed, M passed', 'N error' orderings.
    """
    results: list[dict[str, Any]] = []
    for line in output.splitlines():
        match = _PYTEST_LINE.match(line.strip())
        if not match:
            continue
        parts = {
            word.lower(): int(count)
            for count, word in re.findall(r"(\d+)\s+([A-Za-z]+)", match.group("parts"))
        }
        if not parts:
            continue
        results.append(
            {
                "source": "worker_output",
                "passed": parts.get("passed", 0),
                "failed": parts.get("failed", 0) + parts.get("error", 0),
                "summary": line.strip()[:200],
            }
        )
    return results


def _shrink_events(events: list[dict[str, Any]], scale: float) -> list[dict[str, Any]]:
    """At reduced scale, also cap each event's worker output line (tool results
    are routinely several KB each and dominate recent_events)."""
    if scale >= 1.0:
        return events
    cap = max(200, int(2_000 * scale))
    for event in events:
        payload = event.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("line"), str):
            line = payload["line"]
            if len(line) > cap:
                payload["line"] = line[:cap] + f"... ({len(line)} chars)"
    return events


class ObservationBuilder:
    def __init__(self, store: RunStore, config: FactoryConfig) -> None:
        self.store = store
        self.config = config

    async def build(self, state: FactoryState, *, scale: float = 1.0) -> FactoryObservation:
        """Build the observation; ``scale`` < 1 shrinks every bound proportionally.

        The runtime rebuilds at a smaller scale when the assessment model
        rejects an observation as too large (long multi-worker runs).
        """
        scale = min(1.0, max(0.05, scale))

        def bound(limit: int) -> int:
            return max(100, int(limit * scale))

        field_limit = bound(self.config.field_limit)
        diff_limit = bound(self.config.diff_limit)
        output_limit = bound(self.config.output_limit)
        history_limit = max(1, int(self.config.worker_history_limit * scale))
        event_limit = max(1, int(self.config.event_history_limit * scale))
        repository = Path(state.repository)
        agents_md_path, agents_md_instructions = _repository_agents_md(repository, field_limit)
        # Git evidence is independent, so gather it without serial subprocess latency.
        # --untracked-files=all: without it a new directory collapses to `?? pkg/`
        # and the untracked-evidence reader sees none of the files inside it.
        status_task = asyncio.create_task(
            _git(
                repository,
                "status",
                "--short",
                "--untracked-files=all",
                limit=field_limit,
            )
        )
        diff_task = asyncio.create_task(
            _git(repository, "diff", "--no-ext-diff", limit=diff_limit)
        )
        names_task = asyncio.create_task(
            _git(repository, "diff", "--name-only", limit=field_limit)
        )
        git_status, git_diff, names = await asyncio.gather(status_task, diff_task, names_task)

        active = [worker for worker in state.workers if worker.worker_id in state.active_workers]
        history = state.workers[-history_limit:]
        latest = history[-1] if history else None
        elapsed = max(0.0, (datetime.now(UTC) - state.started_at).total_seconds())

        return FactoryObservation(
            original_job=_tail(state.job, field_limit),
            run_id=state.run_id,
            factory_status=state.status.value,
            iteration=state.iteration,
            active_workers=[_bounded_worker(worker, output_limit) for worker in active],
            worker_history=[
                _bounded_worker(worker, output_limit) for worker in history
            ],
            latest_worker_output=(
                _tail(f"{latest.stdout}\n{latest.stderr}", output_limit)
                if latest
                else ""
            ),
            worker_exit_status={worker.worker_id: worker.exit_code for worker in history},
            worker_elapsed_seconds={
                worker.worker_id: worker.duration_seconds or 0.0 for worker in active
            },
            git_status=git_status,
            git_diff=git_diff,
            untracked_evidence=_untracked_evidence(
                repository, git_status, min(diff_limit, bound(12_000))
            ),
            changed_files=[line for line in names.splitlines() if line][
                : history_limit * 10
            ],
            agents_md_path=agents_md_path,
            agents_md_instructions=agents_md_instructions,
            test_results=_pytest_summary_from_output(
                (latest.stdout or "") + "\n" + (latest.stderr or "")
            )
            if latest
            else [],
            verification_results=[
                result.model_dump(mode="json") for result in state.verification_results
            ],
            recent_events=_shrink_events(
                self.store.recent_event_dicts(state.run_id, event_limit), scale
            ),
            previous_assessment=state.latest_assessment,
            previous_intervention=state.latest_intervention,
            attempts=len(state.workers),
            failures=state.errors[-history_limit:],
            elapsed_factory_seconds=elapsed,
        )

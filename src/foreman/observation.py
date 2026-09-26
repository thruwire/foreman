from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from foreman.config import FactoryConfig
from foreman.models import FactoryState, ForemanResult, Intervention, WorkerRecord
from foreman.persistence import RunStore


class FactoryObservation(BaseModel):
    """A compact, bounded snapshot of the evidence visible to Foreman."""

    model_config = ConfigDict(extra="forbid")

    original_job: str
    run_id: str
    factory_status: str
    iteration: int = Field(ge=0)
    routing_bindings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    active_extension_ids: list[str] = Field(default_factory=list)
    extension_snapshot_revisions: dict[str, str] = Field(default_factory=dict)
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
    previous_result: ForemanResult | None
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


def _repository_agents_md(
    repository: Path,
    filenames: tuple[str, ...],
    limit: int,
) -> tuple[str | None, str]:
    """Read the repository-root Codex instructions without retaining them in factory state."""

    for filename in filenames:
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
        "client": worker.client,
        "client_session_id": worker.client_session_id,
        "client_turn_id": worker.client_turn_id,
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


_SENSITIVE_BASENAME_PARTS = ("secret", "token", "password", "passwd", "credential")
_SENSITIVE_BASENAME_EXACT = {".env", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
_SENSITIVE_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".jks", ".keystore")

# Untracked files larger than this are never read; the byte budget below is the
# real bound, this only avoids loading enormous files into memory.
_UNTRACKED_MAX_STAT_BYTES = 1_000_000


def _looks_sensitive(name: str) -> bool:
    """Heuristic: never include content of files whose names suggest secrets."""
    base = name.rsplit("/", 1)[-1].lower()
    if base in _SENSITIVE_BASENAME_EXACT or base.startswith(".env."):
        return True
    if any(part in base for part in _SENSITIVE_BASENAME_PARTS):
        return True
    return base.endswith(_SENSITIVE_SUFFIXES)


def _expand_untracked_dir(repo_root: Path, candidate: str) -> list[str]:
    """Expand a collapsed untracked-directory entry to individual file paths.

    `git status` collapses an entirely untracked directory to a single
    `dir/` entry unless it was asked for `--untracked-files=all`. Expanding
    here keeps new source/test directories visible as evidence regardless of
    how the status text was produced.
    """
    dir_path = repo_root / candidate
    if dir_path.is_symlink() or not dir_path.is_dir():
        return []
    try:
        entries = sorted(
            entry for entry in dir_path.rglob("*") if entry.is_file() and not entry.is_symlink()
        )
    except OSError:
        return []
    names = []
    for entry in entries:
        relative = entry.relative_to(repo_root).as_posix()
        if not _looks_sensitive(relative):
            names.append(relative)
    return names


def _untracked_evidence(
    repository: Path,
    git_status: str,
    *,
    file_limit: int,
    byte_limit: int,
) -> str:
    """Bounded head-of-content for untracked files named in git_status.

    Workers write new tests and sources as untracked files, which `git diff`
    never shows, leaving the supervisor without evidence of new work. Include
    a bounded excerpt of each untracked text file so assessments can see it.

    The bounds are hard: at most `file_limit` files and `byte_limit` bytes of
    file content in total. Untracked directories are expanded to their
    individual files so a worker adding a new source or test directory still
    produces evidence. Binary-looking and unreadable files are skipped,
    symlinks are not followed, paths escaping the repository are ignored, and
    files whose names look sensitive (secrets, keys, tokens, .env files) are
    never included. Truncation is applied to raw bytes before UTF-8 decoding,
    so multibyte content can never exceed the byte budget. Read-only: the
    repository is never mutated.
    """
    repo_root = repository.resolve()
    paths: list[str] = []
    for line in git_status.splitlines():
        stripped = line.strip()
        if not stripped.startswith("?? "):
            continue
        candidate = stripped[3:].strip().strip('"')
        if not candidate or _looks_sensitive(candidate):
            continue
        if candidate.endswith("/"):
            paths.extend(_expand_untracked_dir(repo_root, candidate))
            continue
        paths.append(candidate)
    if not paths:
        return ""

    per_file = max(500, byte_limit // max(1, file_limit))
    chunks: list[str] = []
    collected = 0
    for name in paths[:file_limit]:
        remaining = byte_limit - collected
        if remaining <= 0:
            break
        path = repo_root / name
        try:
            if path.is_symlink() or not path.is_file():
                continue
            resolved = path.resolve()
            if resolved != repo_root and repo_root not in resolved.parents:
                continue  # escapes the repository
            if path.stat().st_size > _UNTRACKED_MAX_STAT_BYTES:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:4096]:
            continue  # binary
        # Truncate the raw bytes before decoding so the byte budget is
        # honored for multibyte text. A codepoint cut by the byte boundary
        # decodes to U+FFFD (3 bytes in UTF-8), so trim the encoded form as
        # well; the emitted excerpt then never exceeds the budget.
        budget = min(per_file, remaining)
        total_bytes = len(raw)
        text = raw[:budget].decode("utf-8", errors="replace")
        encoded = text.encode("utf-8")
        if len(encoded) > budget:
            text = encoded[:budget].decode("utf-8", errors="ignore")
        excerpt = text
        if total_bytes > budget:
            excerpt += f"\n... (truncated, {total_bytes} bytes total)"
        chunks.append(f"--- untracked: {name} ---\n{excerpt}")
        collected += min(total_bytes, budget)
    return "\n".join(chunks)


class ObservationBuilder:
    def __init__(
        self,
        store: RunStore,
        config: FactoryConfig,
        *,
        repository_instruction_files: tuple[str, ...] = (
            "AGENTS.override.md",
            "AGENTS.md",
        ),
    ) -> None:
        self.store = store
        self.config = config
        self.repository_instruction_files = repository_instruction_files

    async def build(self, state: FactoryState) -> FactoryObservation:
        return await build_observation(
            state,
            self.config,
            recent_events=self.store.recent_event_dicts(
                state.run_id, self.config.event_history_limit
            ),
            repository_instruction_files=self.repository_instruction_files,
        )


async def build_observation(
    state: FactoryState,
    config: FactoryConfig,
    *,
    recent_events: list[dict[str, Any]],
    repository_instruction_files: tuple[str, ...] = (
        "AGENTS.override.md",
        "AGENTS.md",
    ),
) -> FactoryObservation:
    """Build an observation from either a managed run or an attached-worker session."""

    repository = Path(state.repository)
    agents_md_path, agents_md_instructions = _repository_agents_md(
        repository,
        repository_instruction_files,
        config.field_limit,
    )
    # Git evidence is independent, so gather it without serial subprocess latency.
    # --untracked-files=all expands untracked directories to individual files;
    # without it git collapses a new directory to a single `dir/` entry and
    # new sources/tests inside would never surface as evidence.
    status_task = asyncio.create_task(
        _git(repository, "status", "--short", "--untracked-files=all", limit=config.field_limit)
    )
    diff_task = asyncio.create_task(
        _git(repository, "diff", "--no-ext-diff", limit=config.diff_limit)
    )
    names_task = asyncio.create_task(
        _git(repository, "diff", "--name-only", limit=config.field_limit)
    )
    git_status, git_diff, names = await asyncio.gather(status_task, diff_task, names_task)

    active = [worker for worker in state.workers if worker.worker_id in state.active_workers]
    history = state.workers[-config.worker_history_limit :]
    latest = history[-1] if history else None
    elapsed = max(0.0, (datetime.now(UTC) - state.started_at).total_seconds())

    return FactoryObservation(
        original_job=_tail(state.job, config.field_limit),
        run_id=state.run_id,
        factory_status=state.status.value,
        iteration=state.iteration,
        routing_bindings=state.routing_bindings,
        active_extension_ids=state.active_extension_ids,
        extension_snapshot_revisions=state.extension_snapshot_revisions,
        active_workers=[_bounded_worker(worker, config.output_limit) for worker in active],
        worker_history=[_bounded_worker(worker, config.output_limit) for worker in history],
        latest_worker_output=(
            _tail(f"{latest.stdout}\n{latest.stderr}", config.output_limit) if latest else ""
        ),
        worker_exit_status={worker.worker_id: worker.exit_code for worker in history},
        worker_elapsed_seconds={
            worker.worker_id: worker.duration_seconds or 0.0 for worker in active
        },
        git_status=git_status,
        git_diff=git_diff,
        untracked_evidence=_untracked_evidence(
            repository,
            git_status,
            file_limit=config.untracked_evidence_file_limit,
            byte_limit=config.untracked_evidence_byte_limit,
        ),
        changed_files=[line for line in names.splitlines() if line][
            : config.worker_history_limit * 10
        ],
        agents_md_path=agents_md_path,
        agents_md_instructions=agents_md_instructions,
        test_results=[],
        verification_results=[
            result.model_dump(mode="json") for result in state.verification_results
        ],
        recent_events=recent_events[-config.event_history_limit :],
        previous_result=state.latest_result,
        previous_intervention=state.latest_intervention,
        attempts=len(state.workers),
        failures=state.errors[-config.worker_history_limit :],
        elapsed_factory_seconds=elapsed,
    )

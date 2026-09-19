from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from pydantic import ValidationError

from foreman.models import FactoryEvent, FactoryState


class PersistenceError(RuntimeError):
    """Raised when persisted factory data cannot be safely read or written."""


class RunStore:
    """Crash-conscious local persistence for one repository's Foreman runs."""

    def __init__(self, repository: Path | str) -> None:
        self.repository = Path(repository).resolve()
        self.runs_dir = self.repository / ".foreman" / "runs"

    def run_dir(self, run_id: str) -> Path:
        if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
            raise PersistenceError("invalid run id")
        return self.runs_dir / run_id

    def initialize(self, state: FactoryState) -> Path:
        self._ensure_local_git_exclude()
        path = self.run_dir(state.run_id)
        path.mkdir(parents=True, exist_ok=False)
        self.save_state(state)
        (path / "events.jsonl").touch(exist_ok=False)
        return path

    def _ensure_local_git_exclude(self) -> None:
        """Keep runtime state out of status without changing the repository's tracked files."""

        exclude = self.repository / ".git" / "info" / "exclude"
        if not exclude.parent.is_dir():
            return
        entry = "/.foreman/"
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if entry in existing.splitlines():
            return
        separator = "" if not existing or existing.endswith("\n") else "\n"
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write(f"{separator}{entry}\n")

    def save_state(self, state: FactoryState) -> None:
        target_dir = self.run_dir(state.run_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        payload = state.model_dump_json(indent=2)
        fd, temporary = tempfile.mkstemp(prefix="state-", suffix=".tmp", dir=target_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target_dir / "state.json")
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def load_state(self, run_id: str) -> FactoryState:
        path = self.run_dir(run_id) / "state.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            # V0.1 briefly persisted this derived property; tolerate those inspectable runs.
            for worker in payload.get("workers", []):
                worker.pop("duration_seconds", None)
            return FactoryState.model_validate(payload)
        except FileNotFoundError as error:
            raise PersistenceError(f"run {run_id!r} was not found") from error
        except (OSError, json.JSONDecodeError, ValidationError, ValueError) as error:
            raise PersistenceError(f"state for run {run_id!r} is malformed: {error}") from error

    def append_event(self, event: FactoryEvent) -> None:
        path = self.run_dir(event.run_id) / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json())
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def load_events(self, run_id: str, *, tolerate_malformed: bool = False) -> list[FactoryEvent]:
        path = self.run_dir(run_id) / "events.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError as error:
            raise PersistenceError(f"events for run {run_id!r} were not found") from error
        events: list[FactoryEvent] = []
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                events.append(FactoryEvent.model_validate_json(line))
            except (ValidationError, ValueError) as error:
                if tolerate_malformed:
                    continue
                raise PersistenceError(
                    f"event line {line_number} for run {run_id!r} is malformed: {error}"
                ) from error
        return events

    def list_states(self) -> Iterable[FactoryState]:
        if not self.runs_dir.exists():
            return []
        states: list[FactoryState] = []
        paths = sorted(
            self.runs_dir.iterdir(),
            key=lambda item: (item.stat().st_mtime, item.name),
            reverse=True,
        )
        for path in paths:
            try:
                states.append(self.load_state(path.name))
            except PersistenceError:
                continue
        return states

    def recent_event_dicts(self, run_id: str, limit: int) -> list[dict[str, object]]:
        events = self.load_events(run_id, tolerate_malformed=True)[-limit:]
        return [json.loads(event.model_dump_json()) for event in events]

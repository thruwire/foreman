from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from foreman.models import EventType, FactoryEvent, WorkerRecord, WorkerType
from foreman.persistence import PersistenceError, RunStore


def test_state_persistence_and_recovery(state, tmp_path) -> None:
    store = RunStore(tmp_path)
    store.initialize(state)
    state.iteration = 3
    store.save_state(state)
    assert store.load_state(state.run_id).iteration == 3


def test_state_recovery_with_worker(state, tmp_path) -> None:
    worker = WorkerRecord(worker_id="worker-1", worker_type=WorkerType.CODING, mission="work")
    state.workers.append(worker)
    state.active_workers.append(worker.worker_id)
    store = RunStore(tmp_path)
    store.initialize(state)
    restored = store.load_state(state.run_id)
    assert restored.workers == [worker]
    assert "duration_seconds" not in (store.run_dir(state.run_id) / "state.json").read_text()


def test_state_write_leaves_no_temporary_file(state, tmp_path) -> None:
    store = RunStore(tmp_path)
    store.initialize(state)
    store.save_state(state)
    assert {path.name for path in store.run_dir(state.run_id).iterdir()} == {
        "state.json",
        "events.jsonl",
    }


def test_jsonl_events(state, tmp_path) -> None:
    store = RunStore(tmp_path)
    store.initialize(state)
    first = FactoryEvent(run_id=state.run_id, event_type=EventType.FACTORY_STARTED)
    second = FactoryEvent(run_id=state.run_id, event_type=EventType.WORKER_STARTED)
    store.append_event(first)
    store.append_event(second)
    assert store.load_events(state.run_id) == [first, second]
    assert len((store.run_dir(state.run_id) / "events.jsonl").read_text().splitlines()) == 2


def test_malformed_state_handling(state, tmp_path) -> None:
    store = RunStore(tmp_path)
    store.initialize(state)
    (store.run_dir(state.run_id) / "state.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(PersistenceError, match="malformed"):
        store.load_state(state.run_id)


def test_malformed_event_handling(state, tmp_path) -> None:
    store = RunStore(tmp_path)
    store.initialize(state)
    event = FactoryEvent(run_id=state.run_id, event_type=EventType.FACTORY_STARTED)
    path = store.run_dir(state.run_id) / "events.jsonl"
    path.write_text(f"{event.model_dump_json()}\n{{broken\n", encoding="utf-8")
    with pytest.raises(PersistenceError, match="line 2"):
        store.load_events(state.run_id)
    assert store.load_events(state.run_id, tolerate_malformed=True) == [event]


def test_invalid_run_id_is_rejected(tmp_path) -> None:
    with pytest.raises(PersistenceError):
        RunStore(tmp_path).run_dir("../escape")


def test_list_states_skips_malformed_run(state, tmp_path) -> None:
    store = RunStore(tmp_path)
    store.initialize(state)
    broken = store.runs_dir / "broken"
    broken.mkdir()
    (broken / "state.json").write_text(json.dumps({"no": "state"}), encoding="utf-8")
    assert list(store.list_states()) == [state]


def test_list_states_breaks_equal_mtime_ties_by_run_id(state, tmp_path, monkeypatch) -> None:
    store = RunStore(tmp_path)
    first = state.model_copy(update={"run_id": "aaa"})
    second = state.model_copy(update={"run_id": "zzz"})
    store.initialize(first)
    store.initialize(second)

    tied_mtime = 1_700_000_000
    os.utime(store.run_dir(first.run_id), (tied_mtime, tied_mtime))
    os.utime(store.run_dir(second.run_id), (tied_mtime, tied_mtime))
    original_iterdir = Path.iterdir

    def iterdir(path):
        if path == store.runs_dir:
            return iter([store.run_dir(first.run_id), store.run_dir(second.run_id)])
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)

    assert [listed.run_id for listed in store.list_states()] == ["zzz", "aaa"]


def test_initialize_locally_excludes_runtime_directory(state, tmp_path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    store = RunStore(tmp_path)
    store.initialize(state)
    status = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--short", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert ".foreman" not in status
    assert "/.foreman/" in (tmp_path / ".git" / "info" / "exclude").read_text()


def test_list_run_ids_and_load_decision_events(tmp_path) -> None:
    store = RunStore(tmp_path)
    assert store.list_run_ids() == []
    assert store.load_decision_events() == []

    # Create two runs: one factory run and one MCP run (no state.json)
    event1 = FactoryEvent(
        run_id="run-1",
        event_type=EventType.FOREMAN_DECIDED,
        payload={"question": "Q1", "answered": True, "choice": "A", "confidence": 0.85},
    )
    event2 = FactoryEvent(
        run_id="run-2",
        event_type=EventType.FACTORY_STARTED,
        payload={},
    )
    event3 = FactoryEvent(
        run_id="run-2",
        event_type=EventType.FOREMAN_DECIDED,
        payload={"question": "Q2", "answered": False, "choice": None, "confidence": 0.60},
    )

    store.append_event(event1)
    store.append_event(event2)
    store.append_event(event3)

    run_ids = store.list_run_ids()
    assert set(run_ids) == {"run-1", "run-2"}

    # All decisions across runs
    decisions = store.load_decision_events()
    assert len(decisions) == 2
    assert {d.payload["question"] for d in decisions} == {"Q1", "Q2"}

    # Decisions for specific run
    run1_decisions = store.load_decision_events(run_id="run-1")
    assert len(run1_decisions) == 1
    assert run1_decisions[0].payload["question"] == "Q1"

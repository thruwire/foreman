from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from foreman.cli import app
from foreman.models import EventType, FactoryEvent, FactoryState
from foreman.persistence import RunStore

runner = CliRunner()


def test_inspect_factory_run_with_state(tmp_path: Path, state: FactoryState) -> None:
    store = RunStore(tmp_path)
    store.initialize(state)
    event = FactoryEvent(
        run_id=state.run_id,
        event_type=EventType.FACTORY_STARTED,
        payload={},
    )
    store.append_event(event)

    result = runner.invoke(app, ["inspect", state.run_id, "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert f"FOREMAN RUN: {state.run_id}" in result.output
    assert "Factory started" in result.output


def test_inspect_decided_run_without_state(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    event = FactoryEvent(
        run_id="serve-session-123",
        event_type=EventType.FOREMAN_DECIDED,
        payload={
            "question": "Use join or subquery?",
            "answered": True,
            "choice": "join",
            "confidence": 0.88,
            "rationale": "chose join",
        },
    )
    store.append_event(event)

    result = runner.invoke(app, ["inspect", "serve-session-123", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "FOREMAN RUN: serve-session-123" in result.output
    assert "Foreman decided: answered" in result.output
    assert "choice: join" in result.output
    assert "confidence: 0.88" in result.output


def test_inspect_abstained_decision(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    event = FactoryEvent(
        run_id="serve-session-124",
        event_type=EventType.FOREMAN_DECIDED,
        payload={
            "question": "Should we force push to main?",
            "answered": False,
            "choice": None,
            "confidence": 0.50,
            "rationale": "Abstained: classified as destructive, which must abstain.",
        },
    )
    store.append_event(event)

    result = runner.invoke(app, ["inspect", "serve-session-124", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "Foreman decided: abstained" in result.output


def test_inspect_nonexistent_run(tmp_path: Path) -> None:
    result = runner.invoke(app, ["inspect", "does-not-exist", "--repo", str(tmp_path)])
    assert result.exit_code == 2
    assert "not found" in result.output

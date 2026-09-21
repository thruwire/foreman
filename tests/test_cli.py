from __future__ import annotations

import json
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


def test_inspect_mcp_run_without_state(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    event = FactoryEvent(
        run_id="mcp-session-123",
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

    result = runner.invoke(app, ["inspect", "mcp-session-123", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "FOREMAN RUN: mcp-session-123" in result.output
    assert "Foreman decided: answered" in result.output
    assert "choice: join" in result.output
    assert "confidence: 0.88" in result.output


def test_inspect_nonexistent_run(tmp_path: Path) -> None:
    result = runner.invoke(app, ["inspect", "does-not-exist", "--repo", str(tmp_path)])
    assert result.exit_code == 2
    assert "not found" in result.output


def test_decisions_no_runs(tmp_path: Path) -> None:
    result = runner.invoke(app, ["decisions", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "No .foreman runs found" in result.output


def test_decisions_empty_runs_json(tmp_path: Path) -> None:
    result = runner.invoke(app, ["decisions", "--repo", str(tmp_path), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["total"] == 0
    assert data["decisions"] == []


def test_decisions_summary_and_table(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    event1 = FactoryEvent(
        run_id="sess-1",
        event_type=EventType.FOREMAN_DECIDED,
        payload={
            "question": "Should we optimize the query?",
            "options": ["yes", "no"],
            "answered": True,
            "choice": "yes",
            "confidence": 0.85,
            "effective_threshold": 0.80,
            "classification": [],
            "effective_denylist": ["destructive", "irreversible", "external", "credentials"],
            "rationale": "Confidence is high",
        },
    )
    event2 = FactoryEvent(
        run_id="sess-2",
        event_type=EventType.FOREMAN_DECIDED,
        payload={
            "question": "Should we force push to main?",
            "options": ["yes", "no"],
            "answered": False,
            "choice": None,
            "confidence": 0.50,
            "effective_threshold": 0.80,
            "classification": ["destructive"],
            "effective_denylist": ["destructive", "irreversible", "external", "credentials"],
            "rationale": "Classified as destructive",
        },
    )
    store.append_event(event1)
    store.append_event(event2)

    # Standard table output
    result = runner.invoke(app, ["decisions", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "Total Decisions" in result.output
    assert "2" in result.output
    assert "ANSWERED" in result.output
    assert "ABSTAINED" in result.output
    assert "optimize" in result.output

    # With --all flag
    all_result = runner.invoke(app, ["decisions", "--repo", str(tmp_path), "--all"])
    assert all_result.exit_code == 0
    assert "optimize" in all_result.output
    assert "query?" in all_result.output

    # JSON output
    json_result = runner.invoke(app, ["decisions", "--repo", str(tmp_path), "--json"])
    assert json_result.exit_code == 0
    data = json.loads(json_result.output)
    assert data["total"] == 2
    assert data["answered"] == 1
    assert data["abstained"] == 1
    assert data["abstain_reasons"]["denylist_interceptions"] == 1
    assert data["abstain_reasons"]["below_threshold"] == 1
    assert data["abstain_reasons"]["model_self_abstained"] == 1
    assert len(data["decisions"]) == 2


def test_decisions_filter_by_run_id(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    event1 = FactoryEvent(
        run_id="sess-1",
        event_type=EventType.FOREMAN_DECIDED,
        payload={"question": "Q1", "answered": True, "choice": "A", "confidence": 0.9},
    )
    event2 = FactoryEvent(
        run_id="sess-2",
        event_type=EventType.FOREMAN_DECIDED,
        payload={"question": "Q2", "answered": False, "choice": None, "confidence": 0.4},
    )
    store.append_event(event1)
    store.append_event(event2)

    result = runner.invoke(
        app,
        ["decisions", "--repo", str(tmp_path), "--run-id", "sess-1", "--json"],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["total"] == 1
    assert data["decisions"][0]["run_id"] == "sess-1"


def test_decisions_multi_repo_discovery(tmp_path: Path) -> None:
    repo1 = tmp_path / "repo1"
    repo2 = tmp_path / "repo2"
    store1 = RunStore(repo1)
    store2 = RunStore(repo2)

    store1.append_event(
        FactoryEvent(
            run_id="r1-sess",
            event_type=EventType.FOREMAN_DECIDED,
            payload={"question": "Repo1 Q", "answered": True, "choice": "A", "confidence": 0.95},
        )
    )
    store2.append_event(
        FactoryEvent(
            run_id="r2-sess",
            event_type=EventType.FOREMAN_DECIDED,
            payload={"question": "Repo2 Q", "answered": False, "choice": None, "confidence": 0.5},
        )
    )

    result = runner.invoke(app, ["decisions", "--repo", str(tmp_path), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["total"] == 2
    repos = {d["repo"] for d in data["decisions"]}
    assert repos == {"repo1", "repo2"}

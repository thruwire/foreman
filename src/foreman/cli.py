from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from foreman.config import FactoryConfig
from foreman.foreman import FakeForemanModel, JevForemanModel
from foreman.mcp import MCPServer
from foreman.models import EventType, FactoryStatus, WorkerType
from foreman.persistence import PersistenceError, RunStore
from foreman.runtime import FactoryRuntime
from foreman.terminal import TerminalRenderer, duration_label, elapsed_label
from foreman.workers import FakeWorker

app = typer.Typer(
    name="foreman",
    help="Supervise Codex workers with a fast semantic decision loop.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


def _run_async(runtime: FactoryRuntime) -> FactoryStatus:
    try:
        state = asyncio.run(runtime.run())
    except KeyboardInterrupt:
        console.print("\nFactory interrupted.")
        raise typer.Exit(code=130) from None
    return state.status


@app.command()
def run(
    repo: Annotated[
        Path,
        typer.Option("--repo", exists=True, file_okay=False, resolve_path=True, help="Repository"),
    ],
    job: Annotated[str, typer.Option("--job", help="Any free-form software job")],
) -> None:
    """Launch a real Codex worker supervised by TypeSafe AI Jev."""

    load_dotenv(repo / ".env", override=False)
    load_dotenv(override=False)
    if not os.getenv("TYPESAFE_API_KEY"):
        console.print(
            "Missing TYPESAFE_API_KEY. Copy .env.example to .env and add your TypeSafe API key."
        )
        raise typer.Exit(code=2)
    config = FactoryConfig.from_environment()
    runtime = FactoryRuntime(
        repository=repo,
        job=job,
        model=JevForemanModel(timeout_seconds=config.jev_timeout_seconds),
        config=config,
        event_sink=TerminalRenderer(console),
    )
    status = _run_async(runtime)
    console.print(f"Run ID: [bold]{runtime.state.run_id}[/bold]")
    if status is not FactoryStatus.FINISHED:
        raise typer.Exit(code=1)


@app.command()
def mcp(
    repo: Annotated[
        Path,
        typer.Option("--repo", exists=True, file_okay=False, resolve_path=True),
    ] = Path("."),
) -> None:
    """Start a stdio MCP server exposing the foreman as a decision tool."""

    load_dotenv(repo / ".env", override=False)
    load_dotenv(override=False)
    if not os.getenv("TYPESAFE_API_KEY"):
        err_console.print(
            "Missing TYPESAFE_API_KEY. Copy .env.example to .env and add your TypeSafe API key."
        )
        raise typer.Exit(code=2)
    config = FactoryConfig.from_environment()
    model = JevForemanModel(timeout_seconds=config.jev_timeout_seconds)
    server = MCPServer(repository=repo, model=model, config=config)
    try:
        asyncio.run(server.serve_stdio())
    except KeyboardInterrupt:
        raise typer.Exit(code=130) from None
    finally:
        asyncio.run(model.close())


@app.command()
def demo(
    repo: Annotated[
        Path,
        typer.Option("--repo", exists=True, file_okay=False, resolve_path=True),
    ] = Path("."),
    job: Annotated[str, typer.Option("--job")] = (
        "Add rate limiting to the API and make sure it is properly tested."
    ),
) -> None:
    """Run a deterministic local simulation with no credentials or external services."""

    config = FactoryConfig(
        assessment_min_interval_seconds=0.04,
        periodic_assessment_seconds=0.25,
        worker_timeout_seconds=5.0,
        overall_timeout_seconds=10.0,
        max_workers=2,
        max_retries=1,
        max_iterations=8,
    )

    def simulated_worker(worker_type: WorkerType) -> FakeWorker:
        if worker_type is WorkerType.VERIFIER:
            return FakeWorker(
                output_lines=["Independently verifying requirements and tests"],
                delay_seconds=0.02,
            )
        return FakeWorker(
            output_lines=["Implementing the requested change", "Running relevant tests"],
            delay_seconds=0.07,
        )

    runtime = FactoryRuntime(
        repository=repo,
        job=job,
        model=FakeForemanModel(),
        config=config,
        worker_factory=simulated_worker,
        event_sink=TerminalRenderer(console),
    )
    status = _run_async(runtime)
    console.print(f"Demo run ID: [bold]{runtime.state.run_id}[/bold]")
    if status is not FactoryStatus.FINISHED:
        raise typer.Exit(code=1)


@app.command("inspect")
def inspect_run(
    run_id: Annotated[str, typer.Argument(help="Run identifier")],
    repo: Annotated[
        Path,
        typer.Option("--repo", exists=True, file_okay=False, resolve_path=True),
    ] = Path("."),
) -> None:
    """Reconstruct a persisted factory timeline."""

    store = RunStore(repo)
    try:
        events = store.load_events(run_id)
    except PersistenceError as error:
        console.print(str(error))
        raise typer.Exit(code=2) from error

    try:
        state = store.load_state(run_id)
    except PersistenceError:
        state = None

    if state is not None:
        finished = state.finished_at or state.updated_at
        duration = max(0.0, (finished - state.started_at).total_seconds())
        console.print(f"[bold]FOREMAN RUN: {state.run_id}[/bold]")
        console.print("Job:")
        console.print(state.job)
        console.print(f"Duration: {duration_label(duration)}")
        console.print(f"Workers: {len(state.workers)}")
        console.print(f"Assessments: {len(state.assessment_history)}")
        console.print(f"Result: {state.status.value}")
        started_at = state.started_at
    else:
        started_at = events[0].timestamp if events else datetime.now(UTC)
        duration = (
            max(0.0, (events[-1].timestamp - started_at).total_seconds())
            if len(events) > 1
            else 0.0
        )
        console.print(f"[bold]FOREMAN RUN: {run_id}[/bold]")
        console.print(f"Duration: {duration_label(duration)}")
        console.print(f"Events: {len(events)}")

    for event in events:
        offset = (event.timestamp - started_at).total_seconds()
        prefix = elapsed_label(offset)
        payload = event.payload
        if event.event_type is EventType.FACTORY_STARTED:
            console.print(f"{prefix}  Factory started")
        elif event.event_type in {EventType.WORKER_STARTED, EventType.VERIFICATION_STARTED}:
            label = (
                "Verification worker"
                if event.event_type is EventType.VERIFICATION_STARTED
                else "Worker"
            )
            console.print(f"{prefix}  {label} {payload.get('worker_id')} started")
        elif event.event_type in {
            EventType.WORKER_COMPLETED,
            EventType.WORKER_FAILED,
            EventType.WORKER_STOPPED,
            EventType.VERIFICATION_COMPLETED,
        }:
            console.print(
                f"{prefix}  {payload.get('worker_id')} {str(payload.get('status', '')).upper()}"
            )
        elif event.event_type is EventType.FOREMAN_ASSESSED:
            assessment = payload["assessment"]
            console.print(f"{prefix}  Foreman assessment")
            for label, key in [
                ("implementation", "implementation_complete"),
                ("requirements", "requirements_satisfied"),
                ("tests", "tests_sufficient"),
                ("verification", "needs_verification"),
                ("progress", "meaningful_progress"),
                ("stuck", "worker_stuck"),
            ]:
                console.print(f"       {label:<16} {float(assessment[key]):.2f}")
        elif event.event_type is EventType.FOREMAN_INTERVENED:
            console.print(f"       {payload.get('action')}: {payload.get('reason', '')}")
        elif event.event_type is EventType.FOREMAN_DECIDED:
            status = "answered" if payload.get("answered") else "abstained"
            console.print(f"{prefix}  Foreman decided: {status}")
            if payload.get("question"):
                q = str(payload.get("question")).replace("\n", " ")
                console.print(f"       question: {q[:80] + '...' if len(q) > 80 else q}")
            console.print(f"       choice: {payload.get('choice')}")
            console.print(
                f"       confidence: {float(payload.get('confidence', 0.0)):.2f}"
            )
            if payload.get("rationale"):
                r = str(payload.get("rationale")).replace("\n", " ")
                console.print(f"       rationale: {r[:100] + '...' if len(r) > 100 else r}")
        elif event.event_type in {EventType.WORKER_STEERED, EventType.WORKER_STEER_FAILED}:
            label = "steered" if event.event_type is EventType.WORKER_STEERED else "steer failed"
            console.print(f"{prefix}  {payload.get('worker_id')} {label}")
        elif event.event_type in {
            EventType.FACTORY_FINISHED,
            EventType.FACTORY_ESCALATED,
            EventType.FACTORY_FAILED,
        }:
            console.print(f"{prefix}  {event.event_type.value}")


@app.command()
def runs(
    repo: Annotated[
        Path,
        typer.Option("--repo", exists=True, file_okay=False, resolve_path=True),
    ] = Path("."),
) -> None:
    """List recent runs stored under the repository's .foreman directory."""

    table = Table("Run", "Status", "Updated", "Workers", "Job")
    for state in RunStore(repo).list_states():
        updated = state.updated_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
        job = state.job.replace("\n", " ")
        table.add_row(state.run_id, state.status.value, updated, str(len(state.workers)), job[:70])
    console.print(table)


def _find_stores(base_path: Path) -> list[tuple[str, RunStore]]:
    """Locate RunStore instances in base_path or its immediate child repositories."""
    base_store = RunStore(base_path)
    if base_store.runs_dir.exists():
        return [(base_path.name, base_store)]

    sub_stores: list[tuple[str, RunStore]] = []
    try:
        for child in sorted(base_path.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                child_store = RunStore(child)
                if child_store.runs_dir.exists():
                    sub_stores.append((child.name, child_store))
    except (OSError, PermissionError):
        pass
    return sub_stores


@app.command("decisions")
def decisions(
    repo: Annotated[
        Path,
        typer.Option(
            "--repo",
            exists=True,
            file_okay=False,
            resolve_path=True,
            help="Repository or directory containing .foreman stores",
        ),
    ] = Path("."),
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Limit results to a specific session or run ID"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output machine-readable JSON"),
    ] = False,
    show_all: Annotated[
        bool,
        typer.Option("--all", "-a", help="Show full question and rationale without truncating"),
    ] = False,
) -> None:
    """Analyze and display autonomous decisions made by the Foreman MCP tool."""

    stores = _find_stores(repo)
    if not stores:
        if json_output:
            console.print(json.dumps({"total": 0, "decisions": []}))
            return
        console.print(f"No .foreman runs found in [bold]{repo}[/bold].")
        return

    records: list[dict[str, Any]] = []
    for repo_name, store in stores:
        for event in store.load_decision_events(run_id=run_id):
            p = event.payload
            records.append(
                {
                    "repo": repo_name,
                    "run_id": event.run_id,
                    "timestamp": event.timestamp.isoformat(),
                    "question": str(p.get("question", "")),
                    "options": p.get("options", []),
                    "answered": bool(p.get("answered", False)),
                    "choice": p.get("choice"),
                    "confidence": float(p.get("confidence", 0.0)),
                    "effective_threshold": float(p.get("effective_threshold", 0.70)),
                    "classification": p.get("classification", []),
                    "effective_denylist": p.get("effective_denylist", []),
                    "rationale": str(p.get("rationale", "")),
                }
            )

    if not records:
        if json_output:
            console.print(json.dumps({"total": 0, "decisions": []}))
            return
        target = f"run {run_id}" if run_id else f"repository {repo}"
        console.print(f"No decisions recorded for [bold]{target}[/bold].")
        return

    total = len(records)
    answered_count = sum(1 for r in records if r["answered"])
    abstained_count = total - answered_count
    answered_pct = (answered_count / total * 100) if total else 0.0
    abstained_pct = (abstained_count / total * 100) if total else 0.0
    avg_conf = sum(r["confidence"] for r in records) / total if total else 0.0

    denylist_count = sum(
        1
        for r in records
        if not r["answered"]
        and bool(set(r["classification"]) & set(r["effective_denylist"]))
    )
    low_conf_count = sum(
        1
        for r in records
        if not r["answered"] and r["confidence"] < r["effective_threshold"]
    )
    self_abstained_count = sum(
        1 for r in records if not r["answered"] and r["choice"] is None
    )

    if json_output:
        summary_data = {
            "total": total,
            "answered": answered_count,
            "answered_percentage": round(answered_pct, 2),
            "abstained": abstained_count,
            "abstained_percentage": round(abstained_pct, 2),
            "average_confidence": round(avg_conf, 3),
            "abstain_reasons": {
                "denylist_interceptions": denylist_count,
                "below_threshold": low_conf_count,
                "model_self_abstained": self_abstained_count,
            },
            "decisions": records,
        }
        console.print(json.dumps(summary_data, indent=2))
        return

    summary_table = Table(
        title="Foreman MCP Decisions Summary",
        show_header=True,
        header_style="bold cyan",
    )
    summary_table.add_column("Metric")
    summary_table.add_column("Value", justify="right")
    summary_table.add_row("Total Decisions", str(total))
    summary_table.add_row(
        "Answered",
        f"[bold green]{answered_count}[/bold green] ({answered_pct:.1f}%)",
    )
    summary_table.add_row(
        "Abstained / Declined",
        f"[bold yellow]{abstained_count}[/bold yellow] ({abstained_pct:.1f}%)",
    )
    summary_table.add_row("Average Confidence", f"{avg_conf:.2f}")
    summary_table.add_row("  - Denylist Interceptions", str(denylist_count))
    summary_table.add_row("  - Confidence Below Threshold", str(low_conf_count))
    summary_table.add_row("  - Model Self-Abstained", str(self_abstained_count))
    console.print(summary_table)

    table = Table(title="Decision Log", show_header=True, header_style="bold magenta")
    if len(stores) > 1:
        table.add_column("Repo", style="dim")
    table.add_column("Run / Session", style="cyan")
    table.add_column("Status")
    table.add_column("Conf / Thresh", justify="right")
    table.add_column("Choice / Verdict")
    table.add_column("Question")
    table.add_column("Rationale")

    for r in records:
        status_text = (
            "[bold green]ANSWERED[/bold green]"
            if r["answered"]
            else "[bold yellow]ABSTAINED[/bold yellow]"
        )
        choice_text = str(r["choice"]) if r["choice"] else "[dim]None[/dim]"
        conf_thresh = f"{r['confidence']:.2f} / {r['effective_threshold']:.2f}"
        q_clean = r["question"].replace("\n", " ")
        rat_clean = r["rationale"].replace("\n", " ")
        q_display = (
            q_clean
            if show_all
            else (q_clean[:60] + "..." if len(q_clean) > 60 else q_clean)
        )
        rat_display = (
            rat_clean
            if show_all
            else (rat_clean[:70] + "..." if len(rat_clean) > 70 else rat_clean)
        )

        row_args = []
        if len(stores) > 1:
            row_args.append(r["repo"])
        row_args.extend(
            [
                r["run_id"],
                status_text,
                conf_thresh,
                choice_text,
                q_display,
                rat_display,
            ]
        )
        table.add_row(*row_args)

    console.print(table)



if __name__ == "__main__":
    app()

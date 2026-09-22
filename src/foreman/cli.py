from __future__ import annotations

import asyncio
import os
from datetime import UTC
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from foreman.config import FactoryConfig
from foreman.foreman import FakeForemanModel, JevForemanModel
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
        state = store.load_state(run_id)
        events = store.load_events(run_id)
    except PersistenceError as error:
        console.print(str(error))
        raise typer.Exit(code=2) from error

    finished = state.finished_at or state.updated_at
    duration = max(0.0, (finished - state.started_at).total_seconds())
    console.print(f"[bold]FOREMAN RUN: {state.run_id}[/bold]")
    console.print("Job:")
    console.print(state.job)
    console.print(f"Duration: {duration_label(duration)}")
    console.print(f"Workers: {len(state.workers)}")
    console.print(f"Evaluations: {len(state.result_history)}")
    console.print(f"Result: {state.status.value}")

    for event in events:
        offset = (event.timestamp - state.started_at).total_seconds()
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
            result = payload.get("result")
            if isinstance(result, dict):
                assessment = {
                    key: value
                    for checks in result["checks"].values()
                    for key, value in checks.items()
                }
            else:
                # Historical event logs used a flat assessment payload.
                assessment = payload["assessment"]
            console.print(f"{prefix}  Foreman evaluation")
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


if __name__ == "__main__":
    app()

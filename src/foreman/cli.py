from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from foreman.config import FactoryConfig
from foreman.extensions import (
    ExtensionError,
    ExtensionIdentity,
    ExtensionManager,
    ExtensionSnapshot,
)
from foreman.foreman import FakeForemanModel, JevForemanModel
from foreman.hook_adapters import hook_adapter
from foreman.hooks import AttachedSessionStore, AttachedWorkerRuntime, HookError, run_hook
from foreman.models import EventType, FactoryStatus, WorkerType
from foreman.paths import foreman_config_path, foreman_data_dir
from foreman.persistence import PersistenceError, RunStore
from foreman.responsibilities import (
    ResponsibilityConfigError,
    configured_registry,
)
from foreman.routing import GlobalResponsibilityRouter, JevResponsibilityRouter
from foreman.runtime import FactoryRuntime
from foreman.terminal import TerminalRenderer, duration_label, elapsed_label
from foreman.workers import FakeWorker

app = typer.Typer(
    name="foreman",
    help="Supervise coding workers with a fast semantic decision loop.",
    no_args_is_help=True,
)
extension_app = typer.Typer(help="Manage configured Foreman extensions.", no_args_is_help=True)
app.add_typer(extension_app, name="extension")
console = Console()


def _run_async(runtime: FactoryRuntime) -> FactoryStatus:
    try:
        state = asyncio.run(runtime.run())
    except KeyboardInterrupt:
        console.print("\nFactory interrupted.")
        raise typer.Exit(code=130) from None
    return state.status


def _extension_manager(data_dir: Path | None) -> ExtensionManager:
    return ExtensionManager(data_dir=data_dir)


@extension_app.command("login")
def extension_login(
    extension_id: Annotated[str, typer.Argument(help="Configured extension id")],
    data_dir: Annotated[
        Path | None,
        typer.Option("--data-dir", file_okay=False, resolve_path=True),
    ] = None,
) -> None:
    """Authenticate an extension and immediately synchronize its snapshot."""

    manager: ExtensionManager | None = None
    try:
        manager = _extension_manager(data_dir)

        async def login_and_close() -> tuple[ExtensionIdentity, ExtensionSnapshot | None]:
            try:
                return await manager.login(extension_id)
            finally:
                await manager.close()

        identity, snapshot = asyncio.run(login_and_close())
    except ExtensionError as error:
        console.print(str(error))
        raise typer.Exit(code=2) from error
    label = identity.display_name or identity.subject
    console.print(f"Authenticated {extension_id} as {label}")
    if snapshot is not None:
        console.print(f"Synchronized revision {snapshot.revision}")


@extension_app.command("sync")
def extension_sync(
    extension_id: Annotated[str, typer.Argument(help="Configured extension id")],
    data_dir: Annotated[
        Path | None,
        typer.Option("--data-dir", file_okay=False, resolve_path=True),
    ] = None,
) -> None:
    """Fetch and atomically cache an extension snapshot."""

    try:
        manager = _extension_manager(data_dir)

        async def sync_and_close() -> ExtensionSnapshot:
            try:
                return await manager.sync(extension_id)
            finally:
                await manager.close()

        snapshot = asyncio.run(sync_and_close())
    except ExtensionError as error:
        console.print(str(error))
        raise typer.Exit(code=2) from error
    console.print(f"Synchronized {extension_id} revision {snapshot.revision}")


@extension_app.command("logout")
def extension_logout(
    extension_id: Annotated[str, typer.Argument(help="Configured extension id")],
    data_dir: Annotated[
        Path | None,
        typer.Option("--data-dir", file_okay=False, resolve_path=True),
    ] = None,
) -> None:
    """Remove an extension login and its cached snapshot."""

    try:
        manager = _extension_manager(data_dir)

        async def logout_and_close() -> None:
            try:
                await manager.logout(extension_id)
            finally:
                await manager.close()

        asyncio.run(logout_and_close())
    except ExtensionError as error:
        console.print(str(error))
        raise typer.Exit(code=2) from error
    console.print(f"Logged out {extension_id}")


@extension_app.command("status")
def extension_status(
    extension_id: Annotated[str | None, typer.Argument(help="Configured extension id")] = None,
    data_dir: Annotated[
        Path | None,
        typer.Option("--data-dir", file_okay=False, resolve_path=True),
    ] = None,
) -> None:
    """Show installed state and cached revisions without using the network."""

    try:
        manager = _extension_manager(data_dir)
        statuses = manager.status()
        asyncio.run(manager.close())
    except ExtensionError as error:
        console.print(str(error))
        raise typer.Exit(code=2) from error
    if extension_id is not None:
        statuses = [item for item in statuses if item["id"] == extension_id]
        if not statuses:
            console.print(f"extension {extension_id!r} is not configured")
            raise typer.Exit(code=2)
    table = Table("Extension", "Login", "Sync", "Snapshot", "Expires")
    for item in statuses:
        expiration = item["snapshot_expires_at"]
        table.add_row(
            str(item["id"]),
            "yes" if item["supports_login"] else "no",
            "yes" if item["supports_sync"] else "no",
            str(item["snapshot_revision"] or "missing"),
            expiration.isoformat() if expiration is not None else "-",
        )
    console.print(table)


@app.command()
def hook(
    client: Annotated[
        str,
        typer.Option(
            "--client",
            help="Coding-assistant hook protocol adapter",
        ),
    ] = "codex",
    data_dir: Annotated[
        Path | None,
        typer.Option(
            "--data-dir",
            file_okay=False,
            resolve_path=True,
            help="Foreman-wide data directory for attached-worker sessions",
        ),
    ] = None,
) -> None:
    """Process one coding-assistant hook event from stdin."""

    extensions: ExtensionManager | None = None
    runtime_started = False
    try:
        central_data_dir = foreman_data_dir(data_dir)
        central_config_path = foreman_config_path(central_data_dir)
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            raise HookError("hook input must be a JSON object")
        adapter = hook_adapter(client)
        event = adapter.parse(payload)
        load_dotenv(override=False)
        config = FactoryConfig.from_environment()
        extensions = ExtensionManager(
            data_dir=central_data_dir,
            config_path=central_config_path,
        )
        activated = extensions.activate(config)
        registrations = activated.responsibilities
        runtime = AttachedWorkerRuntime(
            model=JevForemanModel(timeout_seconds=config.jev_timeout_seconds),
            router=JevResponsibilityRouter(timeout_seconds=config.jev_timeout_seconds),
            responsibilities=configured_registry(
                config,
                additional=(item.implementation for item in registrations),
                additional_configs={
                    item.implementation.id: item.definition for item in registrations
                },
            ),
            config=config,
            store=AttachedSessionStore(
                central_data_dir,
                ttl_seconds=config.hook_session_ttl_seconds,
                lock_timeout_seconds=max(30.0, config.jev_timeout_seconds * 3),
            ),
            routing_groups=activated.routing_groups,
            active_extension_ids=activated.extension_ids,
            extension_snapshot_revisions=dict(activated.snapshot_revisions),
        )

        async def process_hook() -> object:
            try:
                return await run_hook(runtime, event)
            finally:
                await extensions.close()

        runtime_started = True
        outcome = asyncio.run(process_hook())
        output = adapter.render(event, outcome)
    except (
        json.JSONDecodeError,
        ExtensionError,
        HookError,
        ResponsibilityConfigError,
    ) as error:
        if extensions is not None and not runtime_started:
            asyncio.run(extensions.close())
        typer.echo(f"foreman hook: {error}", err=True)
        raise typer.Exit(code=2) from error
    except Exception as error:
        if extensions is not None and not runtime_started:
            asyncio.run(extensions.close())
        typer.echo(f"foreman hook failed: {type(error).__name__}: {error}", err=True)
        raise typer.Exit(code=1) from error
    sys.stdout.write(json.dumps(output, separators=(",", ":")))
    sys.stdout.write("\n")


@app.command()
def run(
    repo: Annotated[
        Path,
        typer.Option("--repo", exists=True, file_okay=False, resolve_path=True, help="Repository"),
    ],
    job: Annotated[str, typer.Option("--job", help="Any free-form software job")],
    responsibilities_dir: Annotated[
        Path | None,
        typer.Option(
            "--responsibilities-dir",
            file_okay=False,
            resolve_path=True,
            help="Optional Foreman-wide responsibility overrides",
        ),
    ] = None,
    data_dir: Annotated[
        Path | None,
        typer.Option(
            "--data-dir",
            file_okay=False,
            resolve_path=True,
            help="Foreman-wide configuration and extension data directory",
        ),
    ] = None,
) -> None:
    """Launch a real Codex worker supervised by TypeSafe AI Jev."""

    # Capture the process-level factory setting before reading the target repository's
    # local environment. A managed repository cannot select Foreman's responsibilities.
    central_responsibilities_dir = responsibilities_dir
    central_data_dir = foreman_data_dir(data_dir)
    central_config_path = foreman_config_path(central_data_dir)
    if central_responsibilities_dir is None:
        configured = os.getenv("FOREMAN_RESPONSIBILITIES_DIR")
        if configured:
            central_responsibilities_dir = Path(configured).expanduser().resolve()

    load_dotenv(repo / ".env", override=False)
    load_dotenv(override=False)
    if not os.getenv("TYPESAFE_API_KEY"):
        console.print(
            "Missing TYPESAFE_API_KEY. Copy .env.example to .env and add your TypeSafe API key."
        )
        raise typer.Exit(code=2)
    config = FactoryConfig.from_environment()
    extensions: ExtensionManager | None = None
    try:
        extensions = ExtensionManager(
            data_dir=central_data_dir,
            config_path=central_config_path,
        )
        activated = extensions.activate(config)
        registrations = activated.responsibilities
        responsibilities = configured_registry(
            config,
            config_dir=central_responsibilities_dir,
            additional=(item.implementation for item in registrations),
            additional_configs={
                item.implementation.id: item.definition for item in registrations
            },
        )
        runtime = FactoryRuntime(
            repository=repo,
            job=job,
            model=JevForemanModel(timeout_seconds=config.jev_timeout_seconds),
            config=config,
            event_sink=TerminalRenderer(console),
            responsibilities=responsibilities,
            router=JevResponsibilityRouter(timeout_seconds=config.jev_timeout_seconds),
            routing_groups=activated.routing_groups,
            active_extension_ids=activated.extension_ids,
            extension_snapshot_revisions=dict(activated.snapshot_revisions),
        )
    except (ExtensionError, ResponsibilityConfigError) as error:
        if extensions is not None:
            asyncio.run(extensions.close())
        console.print(str(error))
        raise typer.Exit(code=2) from error
    try:
        status = _run_async(runtime)
    finally:
        if extensions is not None:
            asyncio.run(extensions.close())
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
        responsibilities=configured_registry(config),
        router=GlobalResponsibilityRouter(),
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
        console.print(f"Evaluations: {len(state.result_history)}")
        if state.active_responsibility_ids:
            console.print(f"Responsibilities: {', '.join(state.active_responsibility_ids)}")
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
        elif event.event_type is EventType.FOREMAN_ROUTED:
            active = payload.get("active_responsibility_ids", [])
            console.print(f"{prefix}  Routed to {', '.join(active)}")
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
        elif event.event_type is EventType.FOREMAN_DECIDED:
            status = "answered" if payload.get("answered") else "abstained"
            console.print(f"{prefix}  Foreman decided: {status}")
            if payload.get("question"):
                q = str(payload.get("question")).replace("\n", " ")
                console.print(f"       question: {q[:80] + '...' if len(q) > 80 else q}")
            console.print(f"       choice: {payload.get('choice')}")
            console.print(f"       confidence: {float(payload.get('confidence', 0.0)):.2f}")
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


if __name__ == "__main__":
    app()

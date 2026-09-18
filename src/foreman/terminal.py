from __future__ import annotations

from datetime import timedelta

from rich.console import Console

from foreman.models import EventType, FactoryEvent


class TerminalRenderer:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def __call__(self, event: FactoryEvent) -> None:
        payload = event.payload
        if event.event_type is EventType.FACTORY_STARTED:
            self.console.print("[bold]FOREMAN[/bold]")
            self.console.print("Job:")
            self.console.print(payload.get("job", ""))
            self.console.print("Factory started.")
        elif event.event_type in {EventType.WORKER_STARTED, EventType.VERIFICATION_STARTED}:
            label = (
                "Verification worker"
                if event.event_type is EventType.VERIFICATION_STARTED
                else "Worker"
            )
            self.console.print(f"{label} {payload.get('worker_id')} started.")
            self.console.print("Codex is working... Foreman is watching independently.")
        elif event.event_type in {
            EventType.WORKER_COMPLETED,
            EventType.WORKER_FAILED,
            EventType.WORKER_STOPPED,
            EventType.VERIFICATION_COMPLETED,
        }:
            self.console.print(
                f"{payload.get('worker_id')} {str(payload.get('status', '')).replace('_', ' ')}."
            )
        elif event.event_type is EventType.FOREMAN_OBSERVED:
            self.console.print("Watching factory floor...")
        elif event.event_type is EventType.FOREMAN_ASSESSED:
            assessment = payload["assessment"]
            self.console.print("[bold]FOREMAN ASSESSMENT[/bold]")
            self.console.print("Job")
            for label, key in [
                ("Implementation", "implementation_complete"),
                ("Requirements", "requirements_satisfied"),
                ("Tests", "tests_sufficient"),
                ("Verification needed", "needs_verification"),
                ("Ready to finish", "ready_to_finish"),
            ]:
                self.console.print(f"  {label:<23} {float(assessment[key]):>3.0%}")
            self.console.print("Factory floor")
            for label, key in [
                ("Meaningful progress", "meaningful_progress"),
                ("Worker stuck", "worker_stuck"),
                ("Work off track", "work_off_track"),
                ("Needs human", "needs_human"),
            ]:
                self.console.print(f"  {label:<23} {float(assessment[key]):>3.0%}")
        elif event.event_type is EventType.FOREMAN_INTERVENED:
            self.console.print("Decision")
            self.console.print(f"  [bold]{payload.get('action')}[/bold]")
            self.console.print(f"  {payload.get('reason', '')}")
        elif event.event_type is EventType.WORKER_STEERED:
            self.console.print(f"Guidance sent to {payload.get('worker_id')}:")
            self.console.print(payload.get("message", ""))
        elif event.event_type is EventType.WORKER_STEER_FAILED:
            self.console.print(f"Could not steer {payload.get('worker_id')}; monitoring continues.")
        elif event.event_type is EventType.FACTORY_FINISHED:
            self.console.print("Factory complete.")
        elif event.event_type is EventType.FACTORY_ESCALATED:
            self.console.print(f"Factory escalated: {payload.get('reason')}")
        elif event.event_type is EventType.FACTORY_FAILED:
            self.console.print(f"Factory failed: {payload.get('reason')}")


def elapsed_label(seconds: float) -> str:
    total = int(max(0, seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def duration_label(seconds: float) -> str:
    return str(timedelta(seconds=int(max(0, seconds))))

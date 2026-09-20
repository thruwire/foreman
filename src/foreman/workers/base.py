from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from foreman.models import EventType, WorkerRecord

EventCallback = Callable[[EventType, dict[str, object]], Awaitable[None]]


def worker_environment() -> dict[str, str]:
    """Return the inherited environment without Foreman's TypeSafe credentials."""

    return {name: value for name, value in os.environ.items() if not name.startswith("TYPESAFE_")}


def codex_environment() -> dict[str, str]:
    """Backward-compatible alias for :func:`worker_environment`."""

    return worker_environment()


class Worker(Protocol):
    """Extension point for coding-agent backends.

    A worker drives one coding agent subprocess (or session) for a single
    mission. The runtime only depends on these three coroutines, so new
    backends — another CLI, a remote agent, a container — can be added
    without touching the supervision loop:

    - ``supports_steering`` declares whether the worker has a live input
      channel. The policy uses this capability instead of backend names.
    - ``run`` starts the agent with ``record.mission`` as its prompt,
      streams bounded stdout/stderr through ``emit`` while it works, and
      records the terminal status on ``record`` before returning.
    - ``steer`` attempts to deliver supervisory guidance into an
      in-flight agent. Return ``False`` when the backend has no live
      input channel (e.g. non-interactive CLIs); the policy treats a
      non-steerable backend with stop/retry instead.
    - ``terminate`` asks the agent to stop promptly (interrupt first,
      then kill after a bounded grace period).
    """

    supports_steering: bool

    async def run(
        self,
        record: WorkerRecord,
        repository: Path,
        emit: EventCallback,
        timeout_seconds: float,
    ) -> WorkerRecord: ...

    async def terminate(self, reason: str) -> None: ...

    async def steer(self, message: str) -> bool: ...

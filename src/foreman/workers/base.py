from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from foreman.models import EventType, WorkerRecord

EventCallback = Callable[[EventType, dict[str, object]], Awaitable[None]]


class Worker(Protocol):
    async def run(
        self,
        record: WorkerRecord,
        repository: Path,
        emit: EventCallback,
        timeout_seconds: float,
    ) -> WorkerRecord: ...

    async def terminate(self, reason: str) -> None: ...

    async def steer(self, message: str) -> bool: ...

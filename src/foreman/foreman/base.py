from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from foreman.models import ForemanResult
from foreman.observation import FactoryObservation
from foreman.responsibilities import Check


class ForemanModelError(RuntimeError):
    """The semantic supervisor could not produce a valid assessment."""


class ForemanModel(Protocol):
    async def assess(
        self,
        observation: FactoryObservation,
        checks: Sequence[Check],
    ) -> ForemanResult: ...

    async def close(self) -> None: ...

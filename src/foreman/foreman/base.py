from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from foreman.models import Decision, DecisionRequest, ForemanResult
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

    async def decide(self, request: DecisionRequest) -> Decision:
        """Answer one multiple-choice question or abstain.

        The returned Decision is the model's raw judgment; the decision
        policy applies the configured confidence threshold and abstain
        denylist before any answer reaches the caller.
        """
        ...

    async def close(self) -> None: ...

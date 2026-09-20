from __future__ import annotations

from typing import Protocol

from foreman.models import Decision, DecisionRequest, FactoryAssessment
from foreman.observation import FactoryObservation


class ForemanModelError(RuntimeError):
    """The semantic supervisor could not produce a valid assessment."""


class ForemanModel(Protocol):
    async def assess(self, observation: FactoryObservation) -> FactoryAssessment: ...

    async def decide(self, request: DecisionRequest) -> Decision:
        """Answer one multiple-choice question or abstain.

        The returned Decision is the model's raw judgment; the decision
        policy applies the configured confidence threshold and abstain
        denylist before any answer reaches the caller.
        """
        ...

    async def close(self) -> None: ...

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from foreman.request_budget import DecisionRequestBudget


class DecisionModelError(RuntimeError):
    """A provider could not answer the requested predicates."""


@dataclass(frozen=True)
class DecisionResult:
    values: Mapping[str, Any]
    response: Any  # Raw response retained only for metadata-only usage recording.


class DecisionModelAdapter(Protocol):
    """Provider boundary: evaluate named predicates against structured evidence.

    Values are probabilities that each predicate is true, not confidence in a
    selected label. Adapters never select responsibilities or policy actions.
    """

    provider: str
    model: str
    budget: DecisionRequestBudget

    async def evaluate(
        self, state: Mapping[str, Any], questions: Mapping[str, str],
    ) -> DecisionResult: ...

    async def close(self) -> None: ...

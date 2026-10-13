"""Compatibility facade for the Jev decision-model adapter."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from foreman.decision_models.jev import JevDecisionAdapter, parse_jev_values
from foreman.foreman.decision import DecisionForemanModel, normalize_check_results
from foreman.models import ForemanResult
from foreman.request_budget import DecisionRequestBudget
from foreman.responsibilities import Check
from foreman.usage import DecisionUsageRecorder


def parse_jev_response(response: Any, checks: Sequence[Check]) -> ForemanResult:
    values = parse_jev_values(response, [check.key for check in checks])
    return normalize_check_results(values, checks)


class JevForemanModel(DecisionForemanModel):
    def __init__(
        self, *, client: Any | None = None, timeout_seconds: float = 10.0,
        model: str = "jev-latest", budget: DecisionRequestBudget | None = None,
        usage_recorder: DecisionUsageRecorder | None = None,
    ) -> None:
        super().__init__(
            JevDecisionAdapter(client=client, timeout_seconds=timeout_seconds, model=model),
            timeout_seconds=timeout_seconds, budget=budget, usage_recorder=usage_recorder,
        )

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any

from foreman.decision_models.base import DecisionModelAdapter
from foreman.evidence import evidence_for
from foreman.foreman.base import ForemanModelError
from foreman.models import ForemanResult
from foreman.observation import FactoryObservation
from foreman.request_budget import BudgetedRequest, DecisionRequestBudget, RequestBudgetError
from foreman.responsibilities import Check
from foreman.usage import DecisionUsageRecorder

logger = logging.getLogger(__name__)


def normalize_check_results(
    values: Mapping[str, Any],
    checks: Sequence[Check],
) -> ForemanResult:
    """Validate decision model probabilities and group them by owning responsibility."""

    expected = {check.key for check in checks}
    missing = expected - set(values)
    if missing:
        raise ForemanModelError(f"Decision model response omitted: {', '.join(sorted(missing))}")

    grouped: dict[str, dict[str, float]] = {}
    for check in checks:
        raw = values[check.key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ForemanModelError(f"Decision model answer {check.key!r} is not numeric")
        value = float(raw)
        if not isfinite(value):
            raise ForemanModelError(f"Decision model answer {check.key!r} is not finite")
        grouped.setdefault(check.responsibility_id, {})[check.check_id] = min(1.0, max(0.0, value))
    return ForemanResult(checks=grouped)


class DecisionForemanModel:
    """Provider-independent evidence planning and batched predicate assessment."""

    def __init__(
        self, adapter: DecisionModelAdapter, *, timeout_seconds: float = 10.0,
        budget: DecisionRequestBudget | None = None,
        usage_recorder: DecisionUsageRecorder | None = None,
    ) -> None:
        self.adapter = adapter
        self.timeout_seconds = timeout_seconds
        self.budget = budget or adapter.budget
        self.usage_recorder = usage_recorder or DecisionUsageRecorder()

    @property
    def model(self) -> str:
        return self.adapter.model

    @model.setter
    def model(self, value: str) -> None:
        self.adapter.model = value

    async def assess(
        self,
        observation: FactoryObservation,
        checks: Sequence[Check],
    ) -> ForemanResult:
        groups: dict[tuple[str, ...], list[Check]] = {}
        for check in checks:
            groups.setdefault(evidence_for(check), []).append(check)

        plans = []
        try:
            for providers, group in groups.items():
                fitted = self.budget.fit(
                    observation.state_for(providers),
                    {check.key: check.instructions for check in group},
                )
                if "evidence_budget" in fitted.state:
                    logger.info(
                        "Compacted decision model evidence: %s", fitted.measurements,
                    )
                plans.append((fitted, group))
        except RequestBudgetError as error:
            raise ForemanModelError(str(error)) from error

        async def assess_group(request: BudgetedRequest, group: Sequence[Check]) -> ForemanResult:
            result = await asyncio.wait_for(
                self.adapter.evaluate(
                    request.state, {check.key: check.instructions for check in group},
                ),
                timeout=self.timeout_seconds + 0.5,
            )
            self.usage_recorder.record(
                result.response, request, purpose="assessment", model=self.adapter.model,
                provider=self.adapter.provider,
            )
            return normalize_check_results(result.values, group)

        try:
            tasks = [asyncio.create_task(assess_group(request, group)) for request, group in plans]
            try:
                results = await asyncio.gather(*tasks)
            except BaseException:
                # Settle sibling requests before a runtime can close the owned client.
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            merged: dict[str, dict[str, float]] = {}
            for result in results:
                for responsibility_id, values in result.checks.items():
                    merged.setdefault(responsibility_id, {}).update(values)
            return ForemanResult(checks=merged)
        except TimeoutError as error:
            raise ForemanModelError(f"{self.adapter.provider} assessment timed out") from error
        except ForemanModelError:
            raise
        except Exception as error:
            message = f"{self.adapter.provider} assessment failed: {type(error).__name__}: {error}"
            raise ForemanModelError(message) from error

    async def close(self) -> None:
        await self.adapter.close()

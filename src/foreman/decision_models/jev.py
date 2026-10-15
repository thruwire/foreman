from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from foreman.decision_models.base import DecisionModelError, DecisionResult
from foreman.request_budget import JevRequestBudget


def parse_jev_values(response: Any, keys: Sequence[str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    nouls = getattr(response, "nouls", None)
    if nouls is not None:
        for key in keys:
            answer = nouls.get(key)
            if answer is not None:
                values[key] = getattr(answer, "noul", None)
    elif isinstance(response, Mapping):
        answers = response.get("answers", response)
        if isinstance(answers, Mapping):
            for key in keys:
                answer = answers.get(key)
                if isinstance(answer, Mapping):
                    values[key] = answer.get("noul")
                elif answer is not None:
                    values[key] = answer
    return values


class JevDecisionAdapter:
    """TypeSafe system_one/Noul transport, including OpenRouter SDK configuration."""

    provider = "jev"
    budget = JevRequestBudget()

    def __init__(
        self, *, client: Any | None = None, timeout_seconds: float = 10.0,
        model: str = "jev-latest",
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self.timeout_seconds = timeout_seconds
        self.model = model

    def _make_client(self) -> Any:
        try:
            from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy
        except ImportError as error:
            raise DecisionModelError("typesafe-sdk is not installed") from error
        return AsyncTypeSafeClient(
            model=self.model, timeout=self.timeout_seconds,
            retry=RetryPolicy(
                max_retries=2, http_statuses={429, 500, 502, 503, 504},
                respect_retry_after=True, timeout=self.timeout_seconds,
            ),
        )

    async def evaluate(
        self, state: Mapping[str, Any], questions: Mapping[str, str],
    ) -> DecisionResult:
        from typesafe_sdk import Noul

        if self._client is None:
            self._client = self._make_client()
        response = await self._client.system_one(
            state=dict(state),
            questions={key: Noul(instructions=value) for key, value in questions.items()},
            model=self.model, timeout=self.timeout_seconds,
        )
        return DecisionResult(parse_jev_values(response, list(questions)), response)

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        self._client = None

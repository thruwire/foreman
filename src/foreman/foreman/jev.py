from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any

from foreman.foreman.base import ForemanModelError
from foreman.models import ForemanResult
from foreman.observation import FactoryObservation
from foreman.responsibilities import Check


def normalize_check_results(
    values: Mapping[str, Any],
    checks: Sequence[Check],
) -> ForemanResult:
    """Validate Jev probabilities and group them by owning responsibility."""

    expected = {check.key for check in checks}
    missing = expected - set(values)
    if missing:
        raise ForemanModelError(f"Jev response omitted: {', '.join(sorted(missing))}")

    grouped: dict[str, dict[str, float]] = {}
    for check in checks:
        raw = values[check.key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ForemanModelError(f"Jev answer {check.key!r} is not numeric")
        value = float(raw)
        if not isfinite(value):
            raise ForemanModelError(f"Jev answer {check.key!r} is not finite")
        grouped.setdefault(check.responsibility_id, {})[check.check_id] = min(1.0, max(0.0, value))
    return ForemanResult(checks=grouped)


def parse_jev_response(response: Any, checks: Sequence[Check]) -> ForemanResult:
    """Translate official SDK response types (or their test doubles) into our model."""

    values: dict[str, Any] = {}
    nouls = getattr(response, "nouls", None)
    if nouls is not None:
        for check in checks:
            answer = nouls.get(check.key)
            if answer is not None:
                values[check.key] = getattr(answer, "noul", None)
    elif isinstance(response, Mapping):
        answers = response.get("answers", response)
        if isinstance(answers, Mapping):
            for check in checks:
                answer = answers.get(check.key)
                if isinstance(answer, Mapping):
                    values[check.key] = answer.get("noul")
                elif answer is not None:
                    values[check.key] = answer
    return normalize_check_results(values, checks)


class JevForemanModel:
    """Adapter for TypeSafe's official asynchronous Python SDK."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        timeout_seconds: float = 10.0,
        model: str = "jev-latest",
    ) -> None:
        self._client = client
        self.timeout_seconds = timeout_seconds
        self.model = model
        self._owns_client = client is None

    def _make_client(self) -> Any:
        try:
            from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy
        except ImportError as error:
            raise ForemanModelError(
                "typesafe-sdk is not installed; install the project dependencies"
            ) from error
        return AsyncTypeSafeClient(
            model=self.model,
            timeout=self.timeout_seconds,
            retry=RetryPolicy(
                max_retries=2,
                http_statuses={429, 500, 502, 503, 504},
                respect_retry_after=True,
                timeout=self.timeout_seconds,
            ),
        )

    async def assess(
        self,
        observation: FactoryObservation,
        checks: Sequence[Check],
    ) -> ForemanResult:
        try:
            from typesafe_sdk import Noul
        except ImportError as error:
            if self._client is None:
                raise ForemanModelError("typesafe-sdk is not installed") from error

            class Noul:  # type: ignore[no-redef]
                def __init__(self, *, instructions: str) -> None:
                    self.instructions = instructions

        # One API call evaluates every active responsibility's checks in parallel.
        questions = {check.key: Noul(instructions=check.instructions) for check in checks}
        client = self._client or self._make_client()
        if self._client is None:
            self._client = client
        try:
            response = await asyncio.wait_for(
                client.system_one(
                    state=observation.model_dump(mode="json"),
                    questions=questions,
                    model=self.model,
                    timeout=self.timeout_seconds,
                ),
                timeout=self.timeout_seconds + 0.5,
            )
            return parse_jev_response(response, checks)
        except TimeoutError as error:
            raise ForemanModelError("Jev assessment timed out") from error
        except ForemanModelError:
            raise
        except Exception as error:
            message = f"Jev assessment failed: {type(error).__name__}: {error}"
            raise ForemanModelError(message) from error

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            close = getattr(self._client, "aclose", None)
            if close is not None:
                await close()
        self._client = None

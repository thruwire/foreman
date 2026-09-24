from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from math import isfinite
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from foreman.responsibilities import ResponsibilityRegistry


class ResponsibilityRoutingError(RuntimeError):
    """Incoming work could not be routed to responsibilities."""


class RoutingDecision(BaseModel):
    """The responsibilities activated for one incoming unit of work."""

    model_config = ConfigDict(extra="forbid")

    active_responsibility_ids: list[str]
    scores: dict[str, Annotated[float, Field(ge=0.0, le=1.0)]] = Field(default_factory=dict)
    routed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ResponsibilityRouter(Protocol):
    async def route(
        self,
        work: str,
        candidates: ResponsibilityRegistry,
    ) -> RoutingDecision: ...

    async def close(self) -> None: ...


class GlobalResponsibilityRouter:
    """Activate global responsibilities without evaluating conditional routes."""

    async def route(
        self,
        work: str,
        candidates: ResponsibilityRegistry,
    ) -> RoutingDecision:
        del work
        return RoutingDecision(active_responsibility_ids=list(candidates.global_ids()))

    async def close(self) -> None:
        return None


def resolved_responsibility_ids(
    decision: RoutingDecision,
    candidates: ResponsibilityRegistry,
) -> list[str]:
    """Validate a routing decision and return ordered matches plus global responsibilities."""

    candidate_ids = {responsibility.id for responsibility in candidates.responsibilities}
    requested_ids = set(decision.active_responsibility_ids)
    unknown_ids = requested_ids - candidate_ids
    unknown_scores = set(decision.scores) - candidate_ids
    if unknown_ids or unknown_scores:
        unknown = ", ".join(sorted(unknown_ids | unknown_scores))
        raise ResponsibilityRoutingError(
            f"routing decision references unknown responsibilities: {unknown}"
        )
    requested_ids.update(candidates.global_ids())
    return [
        responsibility.id
        for responsibility in candidates.responsibilities
        if responsibility.id in requested_ids
    ]


def _routing_key(responsibility_id: str) -> str:
    return f"responsibility__{responsibility_id}"


def _parse_scores(response: Any, responsibility_ids: list[str]) -> dict[str, float]:
    values: dict[str, Any] = {}
    nouls = getattr(response, "nouls", None)
    if nouls is not None:
        for responsibility_id in responsibility_ids:
            answer = nouls.get(_routing_key(responsibility_id))
            if answer is not None:
                values[responsibility_id] = getattr(answer, "noul", None)
    elif isinstance(response, Mapping):
        answers = response.get("answers", response)
        if isinstance(answers, Mapping):
            for responsibility_id in responsibility_ids:
                answer = answers.get(_routing_key(responsibility_id))
                if isinstance(answer, Mapping):
                    values[responsibility_id] = answer.get("noul")
                elif answer is not None:
                    values[responsibility_id] = answer

    missing = set(responsibility_ids) - set(values)
    if missing:
        raise ResponsibilityRoutingError(
            f"Jev routing response omitted: {', '.join(sorted(missing))}"
        )

    scores: dict[str, float] = {}
    for responsibility_id in responsibility_ids:
        raw = values[responsibility_id]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ResponsibilityRoutingError(
                f"Jev routing answer for {responsibility_id!r} is not numeric"
            )
        score = float(raw)
        if not isfinite(score):
            raise ResponsibilityRoutingError(
                f"Jev routing answer for {responsibility_id!r} is not finite"
            )
        scores[responsibility_id] = min(1.0, max(0.0, score))
    return scores


class JevResponsibilityRouter:
    """Use one Jev call to activate zero or more non-global responsibilities."""

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
            raise ResponsibilityRoutingError(
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

    async def route(
        self,
        work: str,
        candidates: ResponsibilityRegistry,
    ) -> RoutingDecision:
        routed_ids = [
            responsibility.id
            for responsibility in candidates.responsibilities
            if not candidates.route_for(responsibility.id).always
        ]
        if not routed_ids:
            return RoutingDecision(
                active_responsibility_ids=list(candidates.global_ids()),
            )

        try:
            from typesafe_sdk import Noul
        except ImportError as error:
            if self._client is None:
                raise ResponsibilityRoutingError("typesafe-sdk is not installed") from error

            class Noul:  # type: ignore[no-redef]
                def __init__(self, *, instructions: str) -> None:
                    self.instructions = instructions

        questions = {
            _routing_key(responsibility_id): Noul(
                instructions=candidates.route_for(responsibility_id).instructions or ""
            )
            for responsibility_id in routed_ids
        }
        client = self._client or self._make_client()
        if self._client is None:
            self._client = client
        try:
            response = await asyncio.wait_for(
                client.system_one(
                    state={"work": work},
                    questions=questions,
                    model=self.model,
                    timeout=self.timeout_seconds,
                ),
                timeout=self.timeout_seconds + 0.5,
            )
            scores = _parse_scores(response, routed_ids)
        except TimeoutError as error:
            raise ResponsibilityRoutingError("Jev responsibility routing timed out") from error
        except ResponsibilityRoutingError:
            raise
        except Exception as error:
            raise ResponsibilityRoutingError(
                f"Jev responsibility routing failed: {type(error).__name__}: {error}"
            ) from error

        active = set(candidates.global_ids())
        for responsibility_id, score in scores.items():
            if score >= candidates.route_for(responsibility_id).threshold:
                active.add(responsibility_id)
        active_ids = [
            responsibility.id
            for responsibility in candidates.responsibilities
            if responsibility.id in active
        ]
        return RoutingDecision(active_responsibility_ids=active_ids, scores=scores)

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            close = getattr(self._client, "aclose", None)
            if close is not None:
                await close()
        self._client = None

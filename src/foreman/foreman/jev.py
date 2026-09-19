from __future__ import annotations

import asyncio
from collections.abc import Mapping
from math import isfinite
from typing import Any

from foreman.foreman.base import ForemanModelError
from foreman.models import FactoryAssessment
from foreman.observation import FactoryObservation

ASSESSMENT_QUESTIONS: dict[str, str] = {
    "implementation_complete": (
        "Is the implementation work required by the original job complete?"
    ),
    "tests_sufficient": (
        "Does the work have sufficient relevant test coverage and passing verification?"
    ),
    "requirements_satisfied": (
        "Does the current repository satisfy the original free-form job as a whole?"
    ),
    "needs_verification": (
        "Does the current state warrant an independent verification pass before finishing?"
    ),
    "meaningful_progress": (
        "Is the active or most recent worker making meaningful progress toward the job?"
    ),
    "worker_stuck": (
        "Does the active or most recent worker appear stuck, looping, or unable to advance?"
    ),
    "work_off_track": (
        "Is the current work drifting from the original job or making unrelated changes?"
    ),
    "agents_md_drift": (
        "When agents_md_instructions is present, is the active or most recent worker's behavior "
        "or repository work materially inconsistent with those repository instructions? "
        "Answer no when no AGENTS.md instructions are present or the evidence is insufficient."
    ),
    "ready_to_finish": (
        "Given all evidence, is the factory job ready to be declared complete?"
    ),
    "needs_human": (
        "Does this situation require human judgment, credentials, clarification, or permission?"
    ),
}


def normalize_assessment(values: Mapping[str, Any]) -> FactoryAssessment:
    """Validate all ten Noul probabilities and clamp minor numeric overshoot."""

    normalized: dict[str, float] = {}
    missing = set(ASSESSMENT_QUESTIONS) - set(values)
    if missing:
        raise ForemanModelError(f"Jev response omitted: {', '.join(sorted(missing))}")
    for name in ASSESSMENT_QUESTIONS:
        raw = values[name]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ForemanModelError(f"Jev answer {name!r} is not numeric")
        value = float(raw)
        if not isfinite(value):
            raise ForemanModelError(f"Jev answer {name!r} is not finite")
        normalized[name] = min(1.0, max(0.0, value))
    return FactoryAssessment(**normalized)


def parse_jev_response(response: Any) -> FactoryAssessment:
    """Translate official SDK response types (or their test doubles) into our model."""

    values: dict[str, Any] = {}
    nouls = getattr(response, "nouls", None)
    if nouls is not None:
        for name in ASSESSMENT_QUESTIONS:
            answer = nouls.get(name)
            if answer is not None:
                values[name] = getattr(answer, "noul", None)
    elif isinstance(response, Mapping):
        answers = response.get("answers", response)
        if isinstance(answers, Mapping):
            for name in ASSESSMENT_QUESTIONS:
                answer = answers.get(name)
                if isinstance(answer, Mapping):
                    values[name] = answer.get("noul")
                elif answer is not None:
                    values[name] = answer
    return normalize_assessment(values)


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

    async def assess(self, observation: FactoryObservation) -> FactoryAssessment:
        try:
            from typesafe_sdk import Noul
        except ImportError as error:
            if self._client is None:
                raise ForemanModelError("typesafe-sdk is not installed") from error

            class Noul:  # type: ignore[no-redef]
                def __init__(self, *, instructions: str) -> None:
                    self.instructions = instructions

        # One API call evaluates all atomic dimensions in parallel against the same state.
        questions = {
            name: Noul(instructions=instructions)
            for name, instructions in ASSESSMENT_QUESTIONS.items()
        }
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
            return parse_jev_response(response)
        except TimeoutError as error:
            raise ForemanModelError("Jev assessment timed out") from error
        except ForemanModelError:
            raise
        except Exception as error:
            # SDK-specific errors are deliberately translated so the runtime stays SDK-agnostic.
            message = f"Jev assessment failed: {type(error).__name__}: {error}"
            raise ForemanModelError(message) from error

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            close = getattr(self._client, "aclose", None)
            if close is not None:
                await close()
        self._client = None

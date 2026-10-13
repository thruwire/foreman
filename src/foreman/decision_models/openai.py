from __future__ import annotations

import json
import os
from collections.abc import Mapping
from math import isfinite
from typing import Any

from foreman.decision_models.base import DecisionModelError, DecisionResult
from foreman.request_budget import DecisionRequestBudget, json_bytes


def predicate_questions(questions: Mapping[str, str]) -> list[dict[str, str]]:
    return [
        {"type": "predicate", "name": key, "instructions": instructions}
        for key, instructions in questions.items()
    ]


def decision_input(state: Mapping[str, Any]) -> str:
    return json.dumps(dict(state), ensure_ascii=True, allow_nan=False)


class OpenAIRequestBudget(DecisionRequestBudget):
    """Measure the serialized Decisions input and named predicate envelope."""

    def measure(
        self, state: Mapping[str, Any], questions: Mapping[str, str],
    ) -> dict[str, int]:
        payloads = predicate_questions(questions)
        evidence = decision_input(state)
        longest = max(payloads, key=json_bytes, default={})
        return {
            "state_bytes": json_bytes(state),
            "pair_bytes": json_bytes({"input": evidence, "question": longest}),
            "total_bytes": json_bytes({"input": evidence, "questions": payloads}),
        }


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def parse_openai_values(response: Any, questions: Mapping[str, str]) -> dict[str, float]:
    answers = _field(response, "answers")
    if not isinstance(answers, list) or len(answers) != len(questions):
        raise DecisionModelError("OpenAI decision response omitted or added answers")
    values: dict[str, float] = {}
    # Names must agree with request order. Never silently reassign a refused answer.
    for (key, _), answer in zip(questions.items(), answers, strict=True):
        if _field(answer, "name") != key:
            raise DecisionModelError(f"OpenAI decision answer has an unexpected name for {key!r}")
        kind = _field(answer, "type")
        if kind == "refusal":
            raise DecisionModelError(f"OpenAI decision refused predicate {key!r}")
        if kind != "predicate":
            raise DecisionModelError(f"OpenAI decision answer for {key!r} is not a predicate")
        raw = _field(answer, "probability")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise DecisionModelError(f"OpenAI predicate {key!r} is not numeric")
        value = float(raw)
        if not isfinite(value) or not 0.0 <= value <= 1.0:
            raise DecisionModelError(f"OpenAI predicate {key!r} is not a finite probability")
        values[key] = value
    return values


class OpenAIDecisionAdapter:
    """OpenAI's native /v1/decisions API using predicate questions."""

    provider = "openai"
    budget = OpenAIRequestBudget()

    def __init__(
        self, *, client: Any | None = None, timeout_seconds: float = 10.0,
        model: str = "gpt-6-luna",
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self.timeout_seconds = timeout_seconds
        self.model = model

    def _make_client(self) -> Any:
        try:
            from openai import AsyncOpenAI
        except ImportError as error:
            raise DecisionModelError("Install OpenAI support: pip install 'foreman-core[openai]'") \
                from error
        return AsyncOpenAI(
            api_key=os.getenv("FOREMAN_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"),
            timeout=self.timeout_seconds, max_retries=2,
        )

    async def evaluate(
        self, state: Mapping[str, Any], questions: Mapping[str, str],
    ) -> DecisionResult:
        if self._client is None:
            self._client = self._make_client()
        # The SDK's public POST helper works with releases predating decisions.create.
        response = await self._client.post(
            "/decisions", cast_to=object,
            body={"model": self.model, "input": decision_input(state),
                  "questions": predicate_questions(questions)},
        )
        return DecisionResult(parse_openai_values(response, questions), response)

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.close()
        self._client = None

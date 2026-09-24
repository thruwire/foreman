from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any

from foreman.foreman.base import ForemanModelError
from foreman.models import AbstainCategory, Decision, DecisionRequest, ForemanResult
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


ABSTAIN_CATEGORY_QUESTIONS: dict[AbstainCategory, str] = {
    AbstainCategory.DESTRUCTIVE: (
        "Would answering this question, or acting on the answer, delete or corrupt "
        "data or state (removing files, dropping databases, force-pushing, tearing "
        "down infrastructure)?"
    ),
    AbstainCategory.IRREVERSIBLE: (
        "Would answering this question, or acting on the answer, do something that "
        "cannot be taken back (publishing a release, sending notifications or "
        "email, merging)?"
    ),
    AbstainCategory.EXTERNAL: (
        "Would answering this question, or acting on the answer, cause side effects "
        "beyond the local machine (mutating remote APIs, deployments, webhooks, "
        "anything cost-incurring, exfiltration of code or data to third parties)?"
    ),
    AbstainCategory.CREDENTIALS: (
        "Would answering this question, or acting on the answer, touch "
        "authentication material (reading or using tokens and secrets, granting "
        "permissions, signing artifacts)?"
    ),
}

SHOULD_ABSTAIN_INSTRUCTIONS = (
    "Should this question be left to the human instead of answered autonomously? "
    "Answer yes when the question is ambiguous, the options are all poor, the "
    "stakes are high, or the right answer depends on judgment only the human has."
)

_SHOULD_ABSTAIN_NAME = "should_abstain"
_CLASSIFICATION_THRESHOLD = 0.5


def _decision_option_name(index: int) -> str:
    return f"option_{index}"


def _decision_risk_name(category: AbstainCategory) -> str:
    return f"risk_{category.value}"


def _extract_noul_score(response: Any, name: str) -> float:
    """Read one Noul probability from an SDK response or a test double."""

    raw: Any = None
    nouls = getattr(response, "nouls", None)
    if nouls is not None:
        answer = nouls.get(name)
        if answer is not None:
            raw = getattr(answer, "noul", None)
    elif isinstance(response, Mapping):
        answers = response.get("answers", response)
        if isinstance(answers, Mapping):
            answer = answers.get(name)
            if isinstance(answer, Mapping):
                raw = answer.get("noul")
            elif answer is not None:
                raw = answer
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ForemanModelError(f"Jev decision answer {name!r} is not numeric")
    value = float(raw)
    if not isfinite(value):
        raise ForemanModelError(f"Jev decision answer {name!r} is not finite")
    return min(1.0, max(0.0, value))


def build_decision(
    *,
    options: list[str],
    option_scores: list[float],
    risk_scores: dict[AbstainCategory, float],
    abstain_score: float,
) -> Decision:
    """Translate raw Noul scores into a Decision.

    The model classifies the question, picks the best-scoring option, and may
    abstain outright. The decision policy still applies the configured
    confidence threshold and denylist afterwards.
    """

    classification = [
        category
        for category in AbstainCategory
        if risk_scores[category] >= _CLASSIFICATION_THRESHOLD
    ]
    best_index = max(range(len(options)), key=lambda index: option_scores[index])
    scored = ", ".join(
        f"{option!r}={score:.2f}" for option, score in zip(options, option_scores, strict=True)
    )
    if abstain_score >= _CLASSIFICATION_THRESHOLD:
        return Decision(
            choice=None,
            confidence=abstain_score,
            rationale=(
                f"abstained (should_abstain={abstain_score:.2f}); best option "
                f"{options[best_index]!r} scored {option_scores[best_index]:.2f}; "
                f"options: {scored}"
            ),
            classification=classification,
            abstained=True,
        )
    return Decision(
        choice=options[best_index],
        confidence=option_scores[best_index],
        rationale=(
            f"chose {options[best_index]!r} "
            f"(score {option_scores[best_index]:.2f}); options: {scored}"
        ),
        classification=classification,
        abstained=False,
    )


def _noul_type(client: Any | None) -> Any:
    """Return the SDK Noul class, or a test-double stand-in when injected."""

    try:
        from typesafe_sdk import Noul
    except ImportError as error:
        if client is None:
            raise ForemanModelError("typesafe-sdk is not installed") from error

        class Noul:  # type: ignore[no-redef]
            def __init__(self, *, instructions: str) -> None:
                self.instructions = instructions

    return Noul


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
        Noul = _noul_type(self._client)

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

    async def decide(self, request: DecisionRequest) -> Decision:
        """Classify the question, pick the best option, or abstain.

        One API call scores every option, every abstain category, and an
        explicit should-abstain question in parallel. The decision policy
        applies the configured confidence threshold and denylist afterwards.
        """

        Noul = _noul_type(self._client)
        questions = {
            _decision_option_name(index): Noul(
                instructions=(
                    f"Is {option!r} the best answer to the question? Consider the "
                    "question, the other options, and the context."
                )
            )
            for index, option in enumerate(request.options)
        }
        for category, instructions in ABSTAIN_CATEGORY_QUESTIONS.items():
            questions[_decision_risk_name(category)] = Noul(instructions=instructions)
        questions[_SHOULD_ABSTAIN_NAME] = Noul(instructions=SHOULD_ABSTAIN_INSTRUCTIONS)

        client = self._client or self._make_client()
        if self._client is None:
            self._client = client
        try:
            response = await asyncio.wait_for(
                client.system_one(
                    state=request.model_dump(mode="json"),
                    questions=questions,
                    model=self.model,
                    timeout=self.timeout_seconds,
                ),
                timeout=self.timeout_seconds + 0.5,
            )
            option_scores = [
                _extract_noul_score(response, _decision_option_name(index))
                for index in range(len(request.options))
            ]
            risk_scores = {
                category: _extract_noul_score(response, _decision_risk_name(category))
                for category in AbstainCategory
            }
            abstain_score = _extract_noul_score(response, _SHOULD_ABSTAIN_NAME)
            return build_decision(
                options=request.options,
                option_scores=option_scores,
                risk_scores=risk_scores,
                abstain_score=abstain_score,
            )
        except TimeoutError as error:
            raise ForemanModelError("Jev decision timed out") from error
        except ForemanModelError:
            raise
        except Exception as error:
            message = f"Jev decision failed: {type(error).__name__}: {error}"
            raise ForemanModelError(message) from error

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            close = getattr(self._client, "aclose", None)
            if close is not None:
                await close()
        self._client = None

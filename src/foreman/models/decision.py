from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AbstainCategory(StrEnum):
    """Question categories the foreman must never answer on its own.

    These form the safety floor for autonomous decisions: a question
    classified into a denylisted category is handed back to the human
    instead of being answered, no matter how confident the model is.
    """

    DESTRUCTIVE = "destructive"
    """Deletes or corrupts data or state: removing files, dropping databases,
    force-pushing, tearing down infrastructure."""

    IRREVERSIBLE = "irreversible"
    """Cannot be taken back once done, even if nothing is destroyed:
    publishing a release, sending notifications or email, merging."""

    EXTERNAL = "external"
    """Side effects beyond the local machine: mutating remote APIs,
    deployments, webhooks, anything cost-incurring, and exfiltration of
    code or data to third parties."""

    CREDENTIALS = "credentials"
    """Touches authentication material: reading or using tokens and secrets,
    granting permissions, signing artifacts."""


class DecisionRequest(BaseModel):
    """A single question handed to the foreman for an autonomous decision."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=10_000)
    options: list[str] = Field(min_length=1, max_length=20)
    context: str = Field(default="", max_length=20_000)
    risk_hint: str = Field(default="", max_length=2_000)
    extra_abstain_categories: list[AbstainCategory] = Field(default_factory=list)
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class Decision(BaseModel):
    """The foreman model's raw verdict on a DecisionRequest.

    This is the model's judgment only; the decision policy applies the
    configured threshold and denylist before an answer is returned.
    """

    model_config = ConfigDict(extra="forbid")

    choice: str | None = Field(default=None, max_length=5_000)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=5_000)
    classification: list[AbstainCategory] = Field(default_factory=list)
    abstained: bool = False

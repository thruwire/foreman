from __future__ import annotations

from datetime import UTC, datetime
from math import isfinite
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from foreman.models.events import Directive


class ForemanResult(BaseModel):
    """The aggregate output of one Foreman evaluation cycle."""

    model_config = ConfigDict(extra="forbid")

    checks: dict[str, dict[str, Any]] = Field(default_factory=dict)
    proposed_directives: list[Directive] = Field(default_factory=list)
    selected_directive: Directive | None = None
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def check(self, responsibility_id: str, check_id: str) -> Any:
        try:
            return self.checks[responsibility_id][check_id]
        except KeyError as error:
            raise KeyError(f"missing check {responsibility_id}.{check_id}") from error

    def probability(self, responsibility_id: str, check_id: str) -> float:
        value = self.check(responsibility_id, check_id)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"check {responsibility_id}.{check_id} is not numeric")
        probability = float(value)
        if not isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ValueError(f"check {responsibility_id}.{check_id} is not a finite probability")
        return probability

    def with_directives(
        self,
        proposed: list[Directive],
        selected: Directive,
    ) -> ForemanResult:
        return self.model_copy(
            update={
                "proposed_directives": list(proposed),
                "selected_directive": selected,
            },
            deep=True,
        )

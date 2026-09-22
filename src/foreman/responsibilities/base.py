from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from foreman.models import Directive, FactoryState, ForemanResult


@dataclass(frozen=True, slots=True)
class Check:
    """A named semantic check contributed by one responsibility."""

    responsibility_id: str
    check_id: str
    instructions: str

    @property
    def key(self) -> str:
        return f"{self.responsibility_id}__{self.check_id}"


class Responsibility(Protocol):
    """A logical unit that owns checks and proposes directives."""

    id: str

    def checks(self) -> Sequence[Check]: ...

    def directives(self, state: FactoryState, result: ForemanResult) -> Sequence[Directive]: ...


class ResponsibilityRegistry:
    def __init__(self, responsibilities: Iterable[Responsibility]) -> None:
        self._responsibilities = tuple(responsibilities)
        ids = [responsibility.id for responsibility in self._responsibilities]
        if len(ids) != len(set(ids)):
            raise ValueError("responsibility ids must be unique")

        check_keys: set[str] = set()
        checks: list[Check] = []
        for responsibility in self._responsibilities:
            for check in responsibility.checks():
                if check.responsibility_id != responsibility.id:
                    raise ValueError(
                        f"check {check.check_id!r} belongs to {check.responsibility_id!r}, "
                        f"not {responsibility.id!r}"
                    )
                if check.key in check_keys:
                    raise ValueError(f"duplicate check key: {check.key}")
                check_keys.add(check.key)
                checks.append(check)
        self._checks = tuple(checks)

    @property
    def responsibilities(self) -> tuple[Responsibility, ...]:
        return self._responsibilities

    def checks(self) -> tuple[Check, ...]:
        return self._checks

    def directives(self, state: FactoryState, result: ForemanResult) -> list[Directive]:
        proposals: list[Directive] = []
        for responsibility in self._responsibilities:
            for directive in responsibility.directives(state, result):
                if directive.responsibility_id != responsibility.id:
                    raise ValueError(
                        f"directive belongs to {directive.responsibility_id!r}, "
                        f"not {responsibility.id!r}"
                    )
                proposals.append(directive)
        return proposals

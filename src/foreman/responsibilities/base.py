from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

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


@dataclass(frozen=True, slots=True)
class ResponsibilityRoute:
    """Responsibility-owned instructions for deciding whether it handles incoming work."""

    always: bool = True
    instructions: str | None = None
    threshold: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("responsibility routing threshold must be between 0 and 1")
        if not self.always and not (self.instructions and self.instructions.strip()):
            raise ValueError("routed responsibilities require routing instructions")


class Responsibility(Protocol):
    """A logical unit that owns checks and proposes directives."""

    id: str

    def configured(self, settings: Mapping[str, Any]) -> Responsibility: ...

    def configured_checks(self, checks: Sequence[Check]) -> Responsibility: ...

    def route(self) -> ResponsibilityRoute: ...

    def checks(self) -> Sequence[Check]: ...

    def directives(self, state: FactoryState, result: ForemanResult) -> Sequence[Directive]: ...


class ResponsibilityRegistry:
    def __init__(
        self,
        responsibilities: Iterable[Responsibility],
        *,
        routes: dict[str, ResponsibilityRoute] | None = None,
    ) -> None:
        self._responsibilities = tuple(responsibilities)
        ids = [responsibility.id for responsibility in self._responsibilities]
        if len(ids) != len(set(ids)):
            raise ValueError("responsibility ids must be unique")

        supplied_routes = routes or {}
        unknown_routes = set(supplied_routes) - set(ids)
        if unknown_routes:
            names = ", ".join(sorted(unknown_routes))
            raise ValueError(f"routing configured for unknown responsibilities: {names}")
        self._routes: dict[str, ResponsibilityRoute] = {}
        for responsibility in self._responsibilities:
            route_factory = getattr(responsibility, "route", None)
            default_route = (
                route_factory() if callable(route_factory) else ResponsibilityRoute(always=True)
            )
            self._routes[responsibility.id] = supplied_routes.get(responsibility.id, default_route)

        check_keys: set[str] = set()
        checks: list[Check] = []
        for responsibility in self._responsibilities:
            responsibility_checks = tuple(responsibility.checks())
            if not responsibility_checks:
                raise ValueError(f"responsibility {responsibility.id!r} has no checks")
            for check in responsibility_checks:
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

    def route_for(self, responsibility_id: str) -> ResponsibilityRoute:
        try:
            return self._routes[responsibility_id]
        except KeyError as error:
            raise KeyError(f"unknown responsibility: {responsibility_id}") from error

    def global_ids(self) -> tuple[str, ...]:
        return tuple(
            responsibility.id
            for responsibility in self._responsibilities
            if self._routes[responsibility.id].always
        )

    def routed(self, responsibility_ids: Iterable[str]) -> ResponsibilityRegistry:
        selected = set(responsibility_ids)
        known = {responsibility.id for responsibility in self._responsibilities}
        unknown = selected - known
        if unknown:
            raise ValueError(f"unknown routed responsibilities: {', '.join(sorted(unknown))}")
        responsibilities = [
            responsibility
            for responsibility in self._responsibilities
            if responsibility.id in selected
        ]
        return ResponsibilityRegistry(
            responsibilities,
            routes={
                responsibility.id: self._routes[responsibility.id]
                for responsibility in responsibilities
            },
        )

    def repository_instruction_files(self) -> tuple[str, ...]:
        files: list[str] = []
        for responsibility in self._responsibilities:
            configured = getattr(responsibility, "instruction_files", ())
            for filename in configured:
                if filename not in files:
                    files.append(filename)
        return tuple(files)

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

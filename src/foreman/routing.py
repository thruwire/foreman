from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from foreman.responsibilities import ResponsibilityRegistry, ResponsibilityRoute

_ROUTE_GROUP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class ResponsibilityRoutingError(RuntimeError):
    """Incoming work could not be routed to responsibilities."""


class RouteSelection(StrEnum):
    ALL_MATCHES = "all_matches"
    BEST_MATCH = "best_match"


@dataclass(frozen=True, slots=True)
class RouteGroup:
    """A conditional routing group containing responsibilities and nested groups."""

    id: str
    route: ResponsibilityRoute
    responsibility_ids: tuple[str, ...] = ()
    children: tuple[RouteGroup, ...] = ()
    child_selection: RouteSelection = RouteSelection.ALL_MATCHES
    bindings: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if _ROUTE_GROUP_ID.fullmatch(self.id) is None:
            raise ValueError(f"invalid routing group id: {self.id!r}")
        if len(self.responsibility_ids) != len(set(self.responsibility_ids)):
            raise ValueError(f"routing group {self.id!r} repeats a responsibility")
        try:
            encoded = json.dumps(dict(self.bindings), ensure_ascii=False)
        except (TypeError, ValueError) as error:
            raise ValueError(f"routing group {self.id!r} bindings must be JSON-safe") from error
        if len(encoded) > 50_000:
            raise ValueError(f"routing group {self.id!r} bindings exceed 50000 characters")


class RouteTraceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str
    path: list[str]
    selected: bool
    score: Annotated[float, Field(ge=0.0, le=1.0)] | None = None


class RoutingDecision(BaseModel):
    """The responsibilities activated for one incoming unit of work."""

    model_config = ConfigDict(extra="forbid")

    active_responsibility_ids: list[str]
    scores: dict[str, Annotated[float, Field(ge=0.0, le=1.0)]] = Field(default_factory=dict)
    bindings: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    trace: list[RouteTraceEntry] = Field(default_factory=list)
    routed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def bounded_bindings(self) -> RoutingDecision:
        encoded = json.dumps(self.bindings, ensure_ascii=False)
        if len(encoded) > 100_000:
            raise ValueError("routing bindings exceed 100000 characters")
        return self


class ResponsibilityRouter(Protocol):
    async def route(
        self,
        work: str,
        candidates: ResponsibilityRegistry,
    ) -> RoutingDecision: ...

    async def route_groups(
        self,
        work: str,
        groups: Mapping[str, ResponsibilityRoute],
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

    async def route_groups(
        self,
        work: str,
        groups: Mapping[str, ResponsibilityRoute],
    ) -> RoutingDecision:
        del work
        return RoutingDecision(
            active_responsibility_ids=[
                group_id for group_id, route in groups.items() if route.always
            ]
        )


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


def _validate_route_groups(
    groups: tuple[RouteGroup, ...],
    candidates: ResponsibilityRegistry,
    *,
    max_depth: int = 8,
) -> set[str]:
    candidate_ids = {item.id for item in candidates.responsibilities}
    group_ids: set[str] = set()
    assigned: set[str] = set()
    visiting: set[int] = set()

    def visit(group: RouteGroup, depth: int) -> None:
        if depth > max_depth:
            raise ResponsibilityRoutingError(
                f"routing hierarchy exceeds maximum depth of {max_depth}"
            )
        identity = id(group)
        if identity in visiting:
            raise ResponsibilityRoutingError(f"routing hierarchy contains a cycle at {group.id}")
        if group.id in group_ids:
            raise ResponsibilityRoutingError(f"duplicate routing group id: {group.id}")
        group_ids.add(group.id)
        visiting.add(identity)
        unknown = set(group.responsibility_ids) - candidate_ids
        if unknown:
            raise ResponsibilityRoutingError(
                f"routing group {group.id!r} references unknown responsibilities: "
                f"{', '.join(sorted(unknown))}"
            )
        duplicate_assignments = set(group.responsibility_ids) & assigned
        if duplicate_assignments:
            raise ResponsibilityRoutingError(
                "responsibilities may belong to only one routing group: "
                + ", ".join(sorted(duplicate_assignments))
            )
        assigned.update(group.responsibility_ids)
        for child in group.children:
            visit(child, depth + 1)
        visiting.remove(identity)

    for root in groups:
        visit(root, 1)
    return assigned


def grouped_responsibility_ids(
    groups: tuple[RouteGroup, ...],
    candidates: ResponsibilityRegistry,
) -> set[str]:
    """Validate a hierarchy and return responsibility IDs owned by its groups."""

    return _validate_route_groups(groups, candidates)


async def resolve_hierarchical_routing(
    work: str,
    candidates: ResponsibilityRegistry,
    groups: tuple[RouteGroup, ...],
    router: ResponsibilityRouter,
) -> RoutingDecision:
    """Route root responsibilities and recursively expand every matching route group."""

    if not groups:
        decision = await router.route(work, candidates)
        active = resolved_responsibility_ids(decision, candidates)
        return decision.model_copy(update={"active_responsibility_ids": active})

    assigned = _validate_route_groups(groups, candidates)
    root_ids = tuple(
        responsibility.id
        for responsibility in candidates.responsibilities
        if responsibility.id not in assigned
    )
    active_ids: set[str] = set()
    responsibility_scores: dict[str, float] = {}
    bindings: dict[str, dict[str, JsonValue]] = {}
    trace: list[RouteTraceEntry] = []

    async def route_responsibilities(responsibility_ids: tuple[str, ...]) -> None:
        if not responsibility_ids:
            return
        registry = candidates.routed(responsibility_ids)
        decision = await router.route(work, registry)
        active_ids.update(resolved_responsibility_ids(decision, registry))
        overlap = set(responsibility_scores) & set(decision.scores)
        if overlap:
            raise ResponsibilityRoutingError(
                "responsibilities were routed more than once: " + ", ".join(sorted(overlap))
            )
        responsibility_scores.update(decision.scores)

    async def route_group_level(
        level: tuple[RouteGroup, ...],
        selection: RouteSelection,
        parent_path: tuple[str, ...],
    ) -> None:
        if not level:
            return
        route_groups = getattr(router, "route_groups", None)
        if not callable(route_groups):
            raise ResponsibilityRoutingError(
                "configured router does not support hierarchical route groups"
            )
        routes = {group.id: group.route for group in level}
        decision = await route_groups(work, routes)
        known = set(routes)
        unknown = (set(decision.active_responsibility_ids) | set(decision.scores)) - known
        if unknown:
            raise ResponsibilityRoutingError(
                "routing decision references unknown groups: " + ", ".join(sorted(unknown))
            )
        selected = set(decision.active_responsibility_ids)
        selected.update(group.id for group in level if group.route.always)
        if selection is RouteSelection.BEST_MATCH:
            conditional = [
                group
                for group in level
                if not group.route.always and group.id in selected
            ]
            if conditional:
                winner = max(conditional, key=lambda group: decision.scores.get(group.id, -1.0))
                selected.difference_update(group.id for group in conditional)
                selected.add(winner.id)

        for group in level:
            matched = group.id in selected
            path = (*parent_path, group.id)
            trace.append(
                RouteTraceEntry(
                    group_id=group.id,
                    path=list(path),
                    selected=matched,
                    score=decision.scores.get(group.id),
                )
            )
            if not matched:
                continue
            if group.bindings:
                bindings[group.id] = dict(group.bindings)
            await route_responsibilities(group.responsibility_ids)
            await route_group_level(group.children, group.child_selection, path)

    await route_responsibilities(root_ids)
    await route_group_level(groups, RouteSelection.ALL_MATCHES, ())
    ordered_active = [
        responsibility.id
        for responsibility in candidates.responsibilities
        if responsibility.id in active_ids
    ]
    if not ordered_active:
        raise ResponsibilityRoutingError("routing activated no responsibilities")
    return RoutingDecision(
        active_responsibility_ids=ordered_active,
        scores=responsibility_scores,
        bindings=bindings,
        trace=trace,
    )


def _routing_key(candidate_id: str, *, prefix: str = "responsibility") -> str:
    return f"{prefix}__{candidate_id}"


def _parse_scores(
    response: Any,
    candidate_ids: list[str],
    *,
    prefix: str = "responsibility",
) -> dict[str, float]:
    values: dict[str, Any] = {}
    nouls = getattr(response, "nouls", None)
    if nouls is not None:
        for candidate_id in candidate_ids:
            answer = nouls.get(_routing_key(candidate_id, prefix=prefix))
            if answer is not None:
                values[candidate_id] = getattr(answer, "noul", None)
    elif isinstance(response, Mapping):
        answers = response.get("answers", response)
        if isinstance(answers, Mapping):
            for candidate_id in candidate_ids:
                answer = answers.get(_routing_key(candidate_id, prefix=prefix))
                if isinstance(answer, Mapping):
                    values[candidate_id] = answer.get("noul")
                elif answer is not None:
                    values[candidate_id] = answer

    missing = set(candidate_ids) - set(values)
    if missing:
        raise ResponsibilityRoutingError(
            f"Jev routing response omitted: {', '.join(sorted(missing))}"
        )

    scores: dict[str, float] = {}
    for candidate_id in candidate_ids:
        raw = values[candidate_id]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ResponsibilityRoutingError(
                f"Jev routing answer for {candidate_id!r} is not numeric"
            )
        score = float(raw)
        if not isfinite(score):
            raise ResponsibilityRoutingError(
                f"Jev routing answer for {candidate_id!r} is not finite"
            )
        scores[candidate_id] = min(1.0, max(0.0, score))
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

        scores = await self._score(
            work,
            {
                responsibility_id: candidates.route_for(responsibility_id)
                for responsibility_id in routed_ids
            },
            prefix="responsibility",
        )

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

    async def route_groups(
        self,
        work: str,
        groups: Mapping[str, ResponsibilityRoute],
    ) -> RoutingDecision:
        routed = {group_id: route for group_id, route in groups.items() if not route.always}
        scores = await self._score(work, routed, prefix="group")
        active = {group_id for group_id, route in groups.items() if route.always}
        for group_id, score in scores.items():
            if score >= groups[group_id].threshold:
                active.add(group_id)
        return RoutingDecision(
            active_responsibility_ids=[group_id for group_id in groups if group_id in active],
            scores=scores,
        )

    async def _score(
        self,
        work: str,
        routes: Mapping[str, ResponsibilityRoute],
        *,
        prefix: str,
    ) -> dict[str, float]:
        if not routes:
            return {}
        try:
            from typesafe_sdk import Noul
        except ImportError as error:
            if self._client is None:
                raise ResponsibilityRoutingError("typesafe-sdk is not installed") from error

            class Noul:  # type: ignore[no-redef]
                def __init__(self, *, instructions: str) -> None:
                    self.instructions = instructions

        questions = {
            _routing_key(candidate_id, prefix=prefix): Noul(
                instructions=route.instructions or ""
            )
            for candidate_id, route in routes.items()
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
            scores = _parse_scores(response, list(routes), prefix=prefix)
        except TimeoutError as error:
            raise ResponsibilityRoutingError("Jev responsibility routing timed out") from error
        except ResponsibilityRoutingError:
            raise
        except Exception as error:
            raise ResponsibilityRoutingError(
                f"Jev responsibility routing failed: {type(error).__name__}: {error}"
            ) from error

        return scores

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            close = getattr(self._client, "aclose", None)
            if close is not None:
                await close()
        self._client = None

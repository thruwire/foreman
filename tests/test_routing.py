from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from foreman.config import FactoryConfig
from foreman.models import (
    Directive,
    EventType,
    FactoryStatus,
    ForemanResult,
    InterventionType,
)
from foreman.responsibilities import Check, ResponsibilityRegistry, ResponsibilityRoute
from foreman.routing import (
    JevResponsibilityRouter,
    ResponsibilityRoutingError,
    RouteGroup,
    RouteSelection,
    RoutingDecision,
    resolve_hierarchical_routing,
)
from foreman.runtime import FactoryRuntime
from foreman.workers import FakeWorker


@dataclass
class RoutedResponsibility:
    id: str
    instructions: str
    threshold: float = 0.5
    always: bool = False

    def route(self) -> ResponsibilityRoute:
        return ResponsibilityRoute(
            always=self.always,
            instructions=None if self.always else self.instructions,
            threshold=self.threshold,
        )

    def checks(self) -> tuple[Check, ...]:
        return (Check(self.id, "required", f"Is {self.id} required?"),)

    def directives(self, state, result):
        del state, result
        return []


class Client:
    def __init__(
        self,
        scores: dict[str, float],
        *,
        delay: float = 0.0,
        prefix: str = "responsibility",
    ) -> None:
        self.scores = scores
        self.delay = delay
        self.prefix = prefix
        self.calls = []

    async def system_one(self, **kwargs):
        self.calls.append(kwargs)
        await asyncio.sleep(self.delay)
        return SimpleNamespace(
            nouls={
                f"{self.prefix}__{responsibility_id}": SimpleNamespace(noul=score)
                for responsibility_id, score in self.scores.items()
            }
        )


@pytest.mark.asyncio
async def test_router_keeps_globals_and_activates_multiple_matches() -> None:
    global_responsibility = RoutedResponsibility("core.global", "", always=True)
    alpha = RoutedResponsibility(
        "example.alpha", "Does this work match the alpha workflow?", threshold=0.7
    )
    privacy = RoutedResponsibility(
        "compliance.privacy", "Does this work handle personal data?", threshold=0.8
    )
    docs = RoutedResponsibility(
        "quality.docs", "Does this work require documentation?", threshold=0.6
    )
    registry = ResponsibilityRegistry([global_responsibility, alpha, privacy, docs])
    client = Client(
        {
            "example.alpha": 0.91,
            "compliance.privacy": 0.88,
            "quality.docs": 0.32,
        }
    )

    decision = await JevResponsibilityRouter(client=client).route(
        "Update API documentation for personal-data fields", registry
    )

    assert decision.active_responsibility_ids == [
        "core.global",
        "example.alpha",
        "compliance.privacy",
    ]
    assert decision.scores["quality.docs"] == 0.32
    assert len(client.calls) == 1
    assert client.calls[0]["state"] == {"work": "Update API documentation for personal-data fields"}
    assert set(client.calls[0]["questions"]) == {
        "responsibility__example.alpha",
        "responsibility__compliance.privacy",
        "responsibility__quality.docs",
    }


@pytest.mark.asyncio
async def test_router_skips_jev_when_every_responsibility_is_global() -> None:
    client = Client({})
    registry = ResponsibilityRegistry([RoutedResponsibility("core.global", "", always=True)])

    decision = await JevResponsibilityRouter(client=client).route("work", registry)

    assert decision.active_responsibility_ids == ["core.global"]
    assert decision.scores == {}
    assert client.calls == []


@pytest.mark.asyncio
async def test_router_rejects_missing_jev_answers() -> None:
    registry = ResponsibilityRegistry(
        [RoutedResponsibility("example.conditional", "Does the condition apply?")]
    )
    with pytest.raises(ResponsibilityRoutingError, match="omitted"):
        await JevResponsibilityRouter(client=Client({})).route("work", registry)


@pytest.mark.asyncio
async def test_router_translates_timeout() -> None:
    registry = ResponsibilityRegistry(
        [RoutedResponsibility("example.conditional", "Does the condition apply?")]
    )
    with pytest.raises(ResponsibilityRoutingError, match="timed out"):
        await JevResponsibilityRouter(client=Client({}, delay=1), timeout_seconds=0.01).route(
            "work", registry
        )


@pytest.mark.asyncio
async def test_jev_router_scores_route_groups_with_separate_keys() -> None:
    client = Client({"example.group": 0.91}, prefix="group")
    router = JevResponsibilityRouter(client=client)

    decision = await router.route_groups(
        "work",
        {
            "example.group": ResponsibilityRoute(
                always=False,
                instructions="Does this group apply?",
                threshold=0.8,
            )
        },
    )

    assert decision.active_responsibility_ids == ["example.group"]
    assert decision.scores == {"example.group": 0.91}
    assert set(client.calls[0]["questions"]) == {"group__example.group"}


class HierarchicalRouter:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.group_calls: list[list[str]] = []

    async def route(self, work, candidates):
        del work
        active = list(candidates.global_ids())
        scores = {}
        for responsibility in candidates.responsibilities:
            route = candidates.route_for(responsibility.id)
            if route.always:
                continue
            score = self.scores[responsibility.id]
            scores[responsibility.id] = score
            if score >= route.threshold:
                active.append(responsibility.id)
        return RoutingDecision(active_responsibility_ids=active, scores=scores)

    async def route_groups(self, work, groups):
        del work
        self.group_calls.append(list(groups))
        active = []
        scores = {}
        for group_id, route in groups.items():
            if route.always:
                active.append(group_id)
                continue
            score = self.scores[group_id]
            scores[group_id] = score
            if score >= route.threshold:
                active.append(group_id)
        return RoutingDecision(active_responsibility_ids=active, scores=scores)

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_hierarchical_routing_selects_project_locally_and_preserves_globals() -> None:
    responsibilities = ResponsibilityRegistry(
        [
            RoutedResponsibility("core.global", "", always=True),
            RoutedResponsibility("example.review", "", always=True),
            RoutedResponsibility("example.alpha-policy", "", always=True),
            RoutedResponsibility("example.beta-policy", "", always=True),
        ]
    )
    groups = (
        RouteGroup(
            id="example.root",
            route=ResponsibilityRoute(
                always=False,
                instructions="Does this work require the example extension?",
                threshold=0.7,
            ),
            responsibility_ids=("example.review",),
            child_selection=RouteSelection.BEST_MATCH,
            children=(
                RouteGroup(
                    id="example.project-alpha",
                    route=ResponsibilityRoute(
                        always=False,
                        instructions="Does this work belong to project alpha?",
                        threshold=0.6,
                    ),
                    responsibility_ids=("example.alpha-policy",),
                    bindings={"project_id": "alpha"},
                ),
                RouteGroup(
                    id="example.project-beta",
                    route=ResponsibilityRoute(
                        always=False,
                        instructions="Does this work belong to project beta?",
                        threshold=0.6,
                    ),
                    responsibility_ids=("example.beta-policy",),
                    bindings={"project_id": "beta"},
                ),
            ),
        ),
    )
    router = HierarchicalRouter(
        {
            "example.root": 0.95,
            "example.project-alpha": 0.91,
            "example.project-beta": 0.82,
        }
    )

    decision = await resolve_hierarchical_routing(
        "Change project alpha",
        responsibilities,
        groups,
        router,
    )

    assert decision.active_responsibility_ids == [
        "core.global",
        "example.review",
        "example.alpha-policy",
    ]
    assert decision.bindings == {
        "example.project-alpha": {"project_id": "alpha"},
    }
    assert [entry.group_id for entry in decision.trace if entry.selected] == [
        "example.root",
        "example.project-alpha",
    ]
    assert router.group_calls == [
        ["example.root"],
        ["example.project-alpha", "example.project-beta"],
    ]


@pytest.mark.asyncio
async def test_hierarchical_routing_activates_every_matching_branch_by_default() -> None:
    responsibilities = ResponsibilityRegistry(
        [
            RoutedResponsibility("example.alpha", "", always=True),
            RoutedResponsibility("example.beta", "", always=True),
        ]
    )
    groups = (
        RouteGroup(
            id="example.alpha-group",
            route=ResponsibilityRoute(always=False, instructions="Alpha?", threshold=0.5),
            responsibility_ids=("example.alpha",),
        ),
        RouteGroup(
            id="example.beta-group",
            route=ResponsibilityRoute(always=False, instructions="Beta?", threshold=0.5),
            responsibility_ids=("example.beta",),
        ),
    )
    router = HierarchicalRouter(
        {"example.alpha-group": 0.8, "example.beta-group": 0.9}
    )

    decision = await resolve_hierarchical_routing("Both", responsibilities, groups, router)

    assert decision.active_responsibility_ids == ["example.alpha", "example.beta"]


@pytest.mark.asyncio
async def test_hierarchical_routing_rejects_duplicate_responsibility_assignment() -> None:
    responsibilities = ResponsibilityRegistry(
        [RoutedResponsibility("example.shared", "", always=True)]
    )
    groups = (
        RouteGroup(
            id="example.one",
            route=ResponsibilityRoute(always=True),
            responsibility_ids=("example.shared",),
        ),
        RouteGroup(
            id="example.two",
            route=ResponsibilityRoute(always=True),
            responsibility_ids=("example.shared",),
        ),
    )

    with pytest.raises(ResponsibilityRoutingError, match="only one routing group"):
        await resolve_hierarchical_routing(
            "work",
            responsibilities,
            groups,
            HierarchicalRouter({}),
        )


@dataclass
class ConditionalResponsibility:
    id: str = "example.conditional"

    def route(self) -> ResponsibilityRoute:
        return ResponsibilityRoute(
            always=False,
            instructions="Does this work match the conditional workflow?",
            threshold=0.7,
        )

    def checks(self) -> tuple[Check, ...]:
        return (Check(self.id, "required", "Is the additional review required?"),)

    def directives(self, state, result):
        if result.probability(self.id, "required") < 0.8:
            return []
        return [
            Directive(
                action=InterventionType.ESCALATE,
                reason="additional review required",
                assessment_iteration=max(1, state.iteration),
                responsibility_id=self.id,
                priority=1_100,
            )
        ]


class RoutedModel:
    def __init__(self) -> None:
        self.check_ids = []

    async def assess(self, observation, checks):
        del observation
        self.check_ids = [check.responsibility_id for check in checks]
        return ForemanResult(checks={"example.conditional": {"required": 0.95}})

    async def close(self) -> None:
        return None


class StubRouter:
    def __init__(self) -> None:
        self.closed = False

    async def route(self, work, candidates):
        assert work == "Run the conditional workflow"
        assert [item.id for item in candidates.responsibilities] == ["example.conditional"]
        return RoutingDecision(
            active_responsibility_ids=["example.conditional"],
            scores={"example.conditional": 0.93},
        )

    async def close(self) -> None:
        self.closed = True


class FailingRouter(StubRouter):
    async def route(self, work, candidates):
        del work, candidates
        raise ResponsibilityRoutingError("router offline")


@pytest.mark.asyncio
async def test_runtime_routes_before_starting_worker_and_persists_decision(tmp_path) -> None:
    model = RoutedModel()
    router = StubRouter()
    events = []
    runtime = FactoryRuntime(
        repository=tmp_path,
        job="Run the conditional workflow",
        model=model,
        responsibilities=ResponsibilityRegistry([ConditionalResponsibility()]),
        router=router,
        config=FactoryConfig(
            assessment_min_interval_seconds=0,
            periodic_assessment_seconds=0.1,
            worker_timeout_seconds=1,
            overall_timeout_seconds=2,
        ),
        worker_factory=lambda _: FakeWorker(wait_forever=True, output_lines=[]),
        event_sink=events.append,
    )

    state = await runtime.run()

    assert state.status is FactoryStatus.ESCALATED
    assert state.candidate_responsibility_ids == ["example.conditional"]
    assert state.active_responsibility_ids == ["example.conditional"]
    assert state.routing_scores == {"example.conditional": 0.93}
    assert model.check_ids == ["example.conditional"]
    assert router.closed is True
    event_types = [event.event_type for event in events]
    assert event_types.index(EventType.FOREMAN_ROUTED) < event_types.index(EventType.WORKER_STARTED)


@pytest.mark.asyncio
async def test_routing_failure_stops_before_worker_start(tmp_path) -> None:
    router = FailingRouter()
    events = []
    runtime = FactoryRuntime(
        repository=tmp_path,
        job="Run the conditional workflow",
        model=RoutedModel(),
        responsibilities=ResponsibilityRegistry([ConditionalResponsibility()]),
        router=router,
        config=FactoryConfig(overall_timeout_seconds=2),
        worker_factory=lambda _: FakeWorker(wait_forever=True, output_lines=[]),
        event_sink=events.append,
    )

    state = await runtime.run()

    assert state.status is FactoryStatus.FAILED
    assert state.workers == []
    assert state.errors == ["router offline"]
    assert router.closed is True
    assert EventType.WORKER_STARTED not in [event.event_type for event in events]

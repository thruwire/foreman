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
    RoutingDecision,
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
    def __init__(self, scores: dict[str, float], *, delay: float = 0.0) -> None:
        self.scores = scores
        self.delay = delay
        self.calls = []

    async def system_one(self, **kwargs):
        self.calls.append(kwargs)
        await asyncio.sleep(self.delay)
        return SimpleNamespace(
            nouls={
                f"responsibility__{responsibility_id}": SimpleNamespace(noul=score)
                for responsibility_id, score in self.scores.items()
            }
        )


@pytest.mark.asyncio
async def test_router_keeps_globals_and_activates_multiple_matches() -> None:
    global_responsibility = RoutedResponsibility("core.global", "", always=True)
    legal = RoutedResponsibility(
        "compliance.legal", "Does this work require legal review?", threshold=0.7
    )
    privacy = RoutedResponsibility(
        "compliance.privacy", "Does this work handle personal data?", threshold=0.8
    )
    docs = RoutedResponsibility(
        "quality.docs", "Does this work require documentation?", threshold=0.6
    )
    registry = ResponsibilityRegistry([global_responsibility, legal, privacy, docs])
    client = Client(
        {
            "compliance.legal": 0.91,
            "compliance.privacy": 0.88,
            "quality.docs": 0.32,
        }
    )

    decision = await JevResponsibilityRouter(client=client).route(
        "Add consent language to customer terms", registry
    )

    assert decision.active_responsibility_ids == [
        "core.global",
        "compliance.legal",
        "compliance.privacy",
    ]
    assert decision.scores["quality.docs"] == 0.32
    assert len(client.calls) == 1
    assert client.calls[0]["state"] == {"work": "Add consent language to customer terms"}
    assert set(client.calls[0]["questions"]) == {
        "responsibility__compliance.legal",
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
        [RoutedResponsibility("compliance.legal", "Is legal review required?")]
    )
    with pytest.raises(ResponsibilityRoutingError, match="omitted"):
        await JevResponsibilityRouter(client=Client({})).route("work", registry)


@pytest.mark.asyncio
async def test_router_translates_timeout() -> None:
    registry = ResponsibilityRegistry(
        [RoutedResponsibility("compliance.legal", "Is legal review required?")]
    )
    with pytest.raises(ResponsibilityRoutingError, match="timed out"):
        await JevResponsibilityRouter(client=Client({}, delay=1), timeout_seconds=0.01).route(
            "work", registry
        )


@dataclass
class LegalReviewResponsibility:
    id: str = "compliance.legal"

    def route(self) -> ResponsibilityRoute:
        return ResponsibilityRoute(
            always=False,
            instructions="Does this work require legal review?",
            threshold=0.7,
        )

    def checks(self) -> tuple[Check, ...]:
        return (Check(self.id, "required", "Is legal review required?"),)

    def directives(self, state, result):
        if result.probability(self.id, "required") < 0.8:
            return []
        return [
            Directive(
                action=InterventionType.ESCALATE,
                reason="legal review required",
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
        return ForemanResult(checks={"compliance.legal": {"required": 0.95}})

    async def close(self) -> None:
        return None


class StubRouter:
    def __init__(self) -> None:
        self.closed = False

    async def route(self, work, candidates):
        assert work == "Review customer terms"
        assert [item.id for item in candidates.responsibilities] == ["compliance.legal"]
        return RoutingDecision(
            active_responsibility_ids=["compliance.legal"],
            scores={"compliance.legal": 0.93},
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
        job="Review customer terms",
        model=model,
        responsibilities=ResponsibilityRegistry([LegalReviewResponsibility()]),
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
    assert state.candidate_responsibility_ids == ["compliance.legal"]
    assert state.active_responsibility_ids == ["compliance.legal"]
    assert state.routing_scores == {"compliance.legal": 0.93}
    assert model.check_ids == ["compliance.legal"]
    assert router.closed is True
    event_types = [event.event_type for event in events]
    assert event_types.index(EventType.FOREMAN_ROUTED) < event_types.index(EventType.WORKER_STARTED)


@pytest.mark.asyncio
async def test_routing_failure_stops_before_worker_start(tmp_path) -> None:
    router = FailingRouter()
    events = []
    runtime = FactoryRuntime(
        repository=tmp_path,
        job="Review customer terms",
        model=RoutedModel(),
        responsibilities=ResponsibilityRegistry([LegalReviewResponsibility()]),
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

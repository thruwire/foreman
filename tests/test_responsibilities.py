from __future__ import annotations

from dataclasses import dataclass

import pytest

from foreman.config import FactoryConfig
from foreman.models import Directive, FactoryStatus, ForemanResult, InterventionType
from foreman.policy import FactoryPolicy
from foreman.responsibilities import Check, ResponsibilityRegistry
from foreman.runtime import FactoryRuntime
from foreman.workers import FakeWorker


@dataclass
class ExampleReviewResponsibility:
    id: str = "example.review"

    def checks(self) -> tuple[Check, ...]:
        return (
            Check(
                self.id,
                "review_required",
                "Does this work require an additional review before it can continue?",
            ),
        )

    def directives(self, state, result):
        if result.probability(self.id, "review_required") < 0.8:
            return []
        return [
            Directive(
                action=InterventionType.ESCALATE,
                reason="an additional review is required",
                assessment_iteration=max(1, state.iteration),
                responsibility_id=self.id,
                priority=1_100,
                confidence=result.probability(self.id, "review_required"),
            )
        ]


def test_registry_exposes_responsibility_owned_checks() -> None:
    registry = ResponsibilityRegistry([ExampleReviewResponsibility()])

    assert registry.checks() == (
        Check(
            "example.review",
            "review_required",
            "Does this work require an additional review before it can continue?",
        ),
    )


def test_plugged_in_responsibility_proposes_and_wins_directive(state) -> None:
    registry = ResponsibilityRegistry([ExampleReviewResponsibility()])
    result = ForemanResult(checks={"example.review": {"review_required": 0.94}})

    evaluated = FactoryPolicy(FactoryConfig(), registry).evaluate(state, result)

    assert len(evaluated.proposed_directives) == 1
    assert evaluated.selected_directive is not None
    assert evaluated.selected_directive.action is InterventionType.ESCALATE
    assert evaluated.selected_directive.responsibility_id == "example.review"


def test_registry_rejects_duplicate_responsibility_ids() -> None:
    with pytest.raises(ValueError, match="responsibility ids"):
        ResponsibilityRegistry([ExampleReviewResponsibility(), ExampleReviewResponsibility()])


@dataclass
class EmptyResponsibility:
    id: str = "empty"

    def checks(self) -> tuple[Check, ...]:
        return ()

    def directives(self, state, result):
        del state, result
        return []


def test_registry_rejects_responsibility_without_checks() -> None:
    with pytest.raises(ValueError, match="has no checks"):
        ResponsibilityRegistry([EmptyResponsibility()])


@dataclass
class MisownedCheckResponsibility:
    id: str = "one"

    def checks(self) -> tuple[Check, ...]:
        return (Check("another", "check", "question"),)

    def directives(self, state, result):
        return []


def test_registry_rejects_checks_owned_by_another_responsibility() -> None:
    with pytest.raises(ValueError, match="belongs to"):
        ResponsibilityRegistry([MisownedCheckResponsibility()])


class RecordingModel:
    def __init__(self) -> None:
        self.checks = ()

    async def assess(self, observation, checks):
        del observation
        self.checks = tuple(checks)
        return ForemanResult(checks={"example.review": {"review_required": 0.94}})

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_runtime_evaluates_injected_responsibility(tmp_path) -> None:
    responsibility = ExampleReviewResponsibility()
    registry = ResponsibilityRegistry([responsibility])
    model = RecordingModel()
    runtime = FactoryRuntime(
        repository=tmp_path,
        job="Run work that requires an additional review",
        model=model,
        responsibilities=registry,
        config=FactoryConfig(
            assessment_min_interval_seconds=0,
            periodic_assessment_seconds=0.1,
            worker_timeout_seconds=1,
            overall_timeout_seconds=2,
        ),
        worker_factory=lambda _: FakeWorker(wait_forever=True, output_lines=[]),
    )

    state = await runtime.run()

    assert state.status is FactoryStatus.ESCALATED
    assert model.checks == responsibility.checks()
    assert state.latest_result is not None
    assert state.latest_result.selected_directive is not None
    assert state.latest_result.selected_directive.responsibility_id == responsibility.id

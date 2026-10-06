import pytest

from foreman.config import FactoryConfig
from foreman.models import FactoryState, ForemanResult, InterventionType, WorkerRecord, WorkerType
from foreman.responsibilities import (
    ResponsibilityConfigError,
    ResponsibilityFileConfig,
    configured_registry,
)


def definition(**updates):
    return ResponsibilityFileConfig.model_validate({
        "kind": "declarative", "context": "Read the project context.",
        "checks": {"consulted": {"instructions": "Was context read?", "min_threshold": 0.75}},
        **updates,
    })


@pytest.mark.parametrize("action,expected", [
    ("steer", InterventionType.STEER_WORKER), ("stop", InterventionType.STOP_WORKER),
    ("escalate", InterventionType.ESCALATE),
])
def test_failed_criterion_proposes_configured_action(action, expected):
    registry = configured_registry(
        FactoryConfig(), overrides={"example.context": definition(failure_action=action)}
    )
    responsibility = registry.responsibilities[-1]
    state = FactoryState(run_id="test", job="code", repository=".")
    result = ForemanResult(checks={"example.context": {"consulted": 0.5}})
    directive = responsibility.directives(state, result)[0]
    assert directive.action is expected
    assert directive.responsibility_id == "example.context"
    assert directive.reason == "Read the project context."


def test_criteria_satisfied_continue_active_work_and_allow_completed_work():
    registry = configured_registry(FactoryConfig(), overrides={"example.context": definition()})
    responsibility = registry.responsibilities[-1]
    state = FactoryState(
        run_id="test", job="code", repository=".", active_workers=["worker"],
        workers=[WorkerRecord(worker_id="worker", worker_type=WorkerType.CODING, mission="code")],
    )
    result = ForemanResult(checks={"example.context": {"consulted": 0.75}})
    assert responsibility.directives(state, result) == []
    state.active_workers = []
    assert responsibility.directives(state, result)[0].action is InterventionType.FINISH


@pytest.mark.parametrize("overrides,error", [
    ({"example.context": definition(checks={})}, "require checks with min_threshold"),
    ({"example.context": definition(checks={"consulted": {"instructions": "Was context read?"}})},
     "require checks with min_threshold"),
    ({"core.completion": definition()}, "cannot replace an installed implementation"),
    ({"example.context": definition(settings={"arbitrary": True})}, "do not accept settings"),
    ({"example.unknown": ResponsibilityFileConfig()}, "no installed responsibility"),
])
def test_unsupported_definitions_fail_before_assessment(overrides, error):
    with pytest.raises(ResponsibilityConfigError, match=error):
        configured_registry(FactoryConfig(), overrides=overrides)

import json
import subprocess

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from foreman.cli import app
from foreman.config import FactoryConfig
from foreman.extensions import ForemanFileConfig
from foreman.hooks import AttachedSessionStore
from foreman.models import ForemanResult
from foreman.repository_scope import repository_in_scope
from foreman.responsibilities import (
    ResponsibilityConfigError,
    ResponsibilityFileConfig,
    ResponsibilityRoute,
    configured_registry,
    load_responsibility_configs,
)
from foreman.routing import RouteGroup, RoutingDecision, prune_disabled_route_groups

CUSTOM = "example.project-context"
CONTEXT = "Consult the connected project before coding."


def definitions():
    result = {
        key: ResponsibilityFileConfig(enabled=False) for key in load_responsibility_configs()
    }
    result[CUSTOM] = ResponsibilityFileConfig.model_validate({
        "kind": "declarative",
        "always": False,
        "routing_instructions": "Does this prompt require coding or debugging?",
        "routing_threshold": 0.75,
        "context": CONTEXT,
        "checks": {
            "context_consulted": {
                "instructions": "Was the connected project consulted for this work?",
                "min_threshold": 0.75,
            }
        },
    })
    return result


def central_config(tmp_path, *, repositories=None, custom=True):
    overrides = definitions()
    if not custom:
        del overrides[CUSTOM]
    payload = []
    if repositories is not None:
        payload.append("[hooks]\nrepositories = " + json.dumps(repositories))
    for key, definition in overrides.items():
        payload.append(f'[hooks.responsibilities."{key}"]')
        for field in ["enabled", "kind", "always", "routing_instructions",
                      "routing_threshold", "context"]:
            value = getattr(definition, field)
            if value is not None:
                payload.append(f"{field} = " + json.dumps(value))
        for check_id, check in definition.checks.items():
            payload.append(f'[hooks.responsibilities."{key}".checks.{check_id}]')
            payload.append("instructions = " + json.dumps(check.instructions))
            payload.append("min_threshold = " + str(check.min_threshold))
    path = tmp_path / "config.toml"
    path.write_text("\n".join(payload))
    return path


def event(tmp_path, name, **fields):
    return json.dumps({
        "session_id": "configured", "cwd": str(tmp_path), "hook_event_name": name, **fields
    })


def invoke(tmp_path, name, **fields):
    result = CliRunner().invoke(
        app, ["hook", "--data-dir", str(tmp_path / "state")],
        input=event(tmp_path, name, **fields),
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_local_definitions_can_disable_builtin_classes_and_own_one_check():
    registry = configured_registry(
        FactoryConfig(), overrides=definitions(), require_lifecycle=False
    )
    assert [item.id for item in registry.responsibilities] == [CUSTOM]
    assert [check.key for check in registry.checks()] == [CUSTOM + "__context_consulted"]
    assert registry.global_ids() == ()
    assert registry.repository_instruction_files() == ()
    assert registry.context_for(CUSTOM) == CONTEXT
    assert registry.routed([]).context_for(CUSTOM) is None
    with pytest.raises(ResponsibilityConfigError, match="required global"):
        configured_registry(FactoryConfig(), overrides=definitions())


@pytest.mark.parametrize("responsibility_id", ["core.completion", "core.verification"])
def test_attached_overrides_can_disable_each_lifecycle_class(responsibility_id):
    registry = configured_registry(
        FactoryConfig(),
        overrides={responsibility_id: ResponsibilityFileConfig(enabled=False)},
        require_lifecycle=False,
    )
    assert responsibility_id not in registry.global_ids()
    assert len(registry.responsibilities) == 5


def test_attached_assessments_keep_only_local_check_and_finish_when_satisfied(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FOREMAN_CONFIG", str(central_config(tmp_path)))
    calls = []
    closed = []
    scores = [0.1, 0.95, 0.95]

    class Router:
        def __init__(self, **kwargs):
            pass

        async def route(self, work, candidates):
            assert [item.id for item in candidates.responsibilities] == [CUSTOM]
            return RoutingDecision(active_responsibility_ids=[CUSTOM], scores={CUSTOM: 0.95})

        async def close(self):
            closed.append("router")

    class Model:
        def __init__(self, **kwargs):
            pass

        async def assess(self, observation, checks):
            calls.append([check.key for check in checks])
            return ForemanResult(checks={CUSTOM: {"context_consulted": scores.pop(0)}})

        async def close(self):
            closed.append("model")

    monkeypatch.setattr("foreman.cli.JevResponsibilityRouter", Router)
    monkeypatch.setattr("foreman.cli.JevForemanModel", Model)
    output = invoke(tmp_path, "UserPromptSubmit", prompt="Implement a feature")
    assert CONTEXT in output["hookSpecificOutput"]["additionalContext"]
    assert "core.completion" not in output["hookSpecificOutput"]["additionalContext"]
    output = invoke(tmp_path, "PreToolUse", tool_name="Read")
    assert output["hookSpecificOutput"]["additionalContext"] == CONTEXT
    assert invoke(tmp_path, "PostToolUse", tool_name="Read", tool_response="Project read") == {}
    assert invoke(tmp_path, "Stop") == {}
    assert calls == [[CUSTOM + "__context_consulted"]] * 3
    assert len(closed) == 8


def test_unrelated_prompt_clears_previous_work_without_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("FOREMAN_CONFIG", str(central_config(tmp_path)))

    class Router:
        def __init__(self, **kwargs):
            pass

        async def route(self, work, candidates):
            return RoutingDecision(active_responsibility_ids=[CUSTOM] if work == "code" else [])

        async def close(self):
            pass

    class Model:
        def __init__(self, **kwargs):
            pass

        async def assess(self, *args):
            raise AssertionError("Unmatched work must not be assessed")

        async def close(self):
            pass

    monkeypatch.setattr("foreman.cli.JevResponsibilityRouter", Router)
    monkeypatch.setattr("foreman.cli.JevForemanModel", Model)
    invoke(tmp_path, "UserPromptSubmit", prompt="code")
    assert invoke(tmp_path, "UserPromptSubmit", prompt="picnic") == {}
    for name, fields in [("PreToolUse", {"tool_name": "Read"}),
                         ("PostToolUse", {"tool_name": "Read"}), ("Stop", {})]:
        assert invoke(tmp_path, name, **fields) == {}
    assert AttachedSessionStore(tmp_path / "state").load("configured").state is None


@pytest.mark.parametrize("name,fields", [
    ("SessionStart", {}), ("UserPromptSubmit", {"prompt": "code"}),
    ("PreToolUse", {"tool_name": "Read"}), ("PostToolUse", {"tool_name": "Read"}),
    ("Stop", {}), ("SessionEnd", {}),
])
def test_excluded_repository_never_loads_extensions_or_clients(tmp_path, monkeypatch, name, fields):
    monkeypatch.setenv("FOREMAN_CONFIG", str(central_config(tmp_path, repositories=[])))

    def forbidden(*args, **kwargs):
        raise AssertionError("Excluded hooks must not initialize supervision")

    for target in ["ExtensionManager", "JevForemanModel", "JevResponsibilityRouter"]:
        monkeypatch.setattr("foreman.cli." + target, forbidden)
    store = AttachedSessionStore(tmp_path / "state")
    store.save(store.new("configured", tmp_path))
    assert invoke(tmp_path, name, **fields) == {}
    assert not store.path_for("configured").exists()


def test_all_responsibilities_disabled_initializes_no_model(tmp_path, monkeypatch):
    monkeypatch.setenv("FOREMAN_CONFIG", str(central_config(tmp_path, custom=False)))

    def forbidden(*args, **kwargs):
        raise AssertionError("Disabled checks must not initialize model clients")

    monkeypatch.setattr("foreman.cli.JevForemanModel", forbidden)
    monkeypatch.setattr("foreman.cli.JevResponsibilityRouter", forbidden)
    assert invoke(tmp_path, "UserPromptSubmit", prompt="code") == {}
    assert invoke(tmp_path, "Stop") == {}


def test_disabling_an_extension_responsibility_removes_its_empty_route_groups():
    groups = (RouteGroup(
        id="example.root", route=ResponsibilityRoute(always=True),
        children=(
            RouteGroup(id="example.disabled", route=ResponsibilityRoute(always=True),
                       responsibility_ids=("example.disabled-check",)),
            RouteGroup(id="example.enabled", route=ResponsibilityRoute(always=True),
                       responsibility_ids=(CUSTOM,)),
        ),
    ),)
    pruned = prune_disabled_route_groups(groups, {"example.disabled-check"})
    assert [item.id for item in pruned[0].children] == ["example.enabled"]
    assert pruned[0].children[0].responsibility_ids == (CUSTOM,)
    assert len(groups[0].children) == 2


def test_hook_credentials_can_use_central_dotenv_without_a_custom_launcher(tmp_path, monkeypatch):
    # Never discover a developer's real credentials while testing dotenv precedence.
    monkeypatch.setattr("dotenv.main.find_dotenv", lambda: str(tmp_path / ".env"))
    monkeypatch.setenv("FOREMAN_CONFIG", str(central_config(tmp_path, custom=False)))
    state = tmp_path / "state"
    state.mkdir()
    (state / ".env").write_text("TYPESAFE_API_KEY=central-test-key\n")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert invoke(tmp_path, "SessionStart") == {}
    assert __import__("os").environ["TYPESAFE_API_KEY"] == "central-test-key"
    monkeypatch.setenv("TYPESAFE_API_KEY", "process-test-key")
    assert invoke(tmp_path, "SessionStart") == {}
    assert __import__("os").environ["TYPESAFE_API_KEY"] == "process-test-key"


def test_scope_matches_subdirectories_symlinks_and_linked_worktrees(tmp_path):
    def git(repo, *args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    repo = tmp_path / "selected" / "project"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "-c", "user.name=Scope test", "-c", "user.email=scope@example.test",
        "commit", "--allow-empty", "-qm", "initial")
    subdir = repo / "src"
    subdir.mkdir()
    linked = tmp_path / "linked-worktree"
    git(repo, "worktree", "add", "--detach", "-q", str(linked))
    symlink = tmp_path / "link"
    symlink.symlink_to(repo)
    unrelated = tmp_path / "other" / "project"
    unrelated.mkdir(parents=True)
    git(unrelated, "init", "-q")
    for directory in [repo, subdir, linked, symlink]:
        assert repository_in_scope(directory, (str(repo),))
    assert not repository_in_scope(unrelated, (str(repo),))
    assert not repository_in_scope(tmp_path, (str(repo),))
    assert repository_in_scope(tmp_path, None)
    assert not repository_in_scope(tmp_path, ())


def test_relative_scope_and_malformed_definitions_are_rejected():
    with pytest.raises(ValidationError, match="absolute repository paths"):
        ForemanFileConfig.model_validate({"hooks": {"repositories": ["project"]}})
    with pytest.raises(ValidationError, match="invalid responsibility ids"):
        ForemanFileConfig.model_validate({"hooks": {"responsibilities": {"bad/id": {}}}})

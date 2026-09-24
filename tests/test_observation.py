from __future__ import annotations

import json

import pytest

from foreman.config import FactoryConfig
from foreman.models import FactoryState
from foreman.observation import ObservationBuilder
from foreman.persistence import RunStore


@pytest.mark.asyncio
async def test_observation_reads_repository_agents_md_without_persisting_it(tmp_path) -> None:
    marker = "run the repository-specific verification command"
    (tmp_path / "AGENTS.md").write_text(marker, encoding="utf-8")
    state = FactoryState(run_id="run-1", job="job", repository=str(tmp_path))
    store = RunStore(tmp_path)
    store.initialize(state)

    observation = await ObservationBuilder(store, FactoryConfig()).build(state)

    assert observation.agents_md_path == "AGENTS.md"
    assert observation.agents_md_instructions == marker
    persisted = json.dumps(
        {
            "state": json.loads((store.run_dir(state.run_id) / "state.json").read_text()),
            "events": (store.run_dir(state.run_id) / "events.jsonl").read_text(),
        }
    )
    assert marker not in persisted


@pytest.mark.asyncio
async def test_observation_rereads_agents_override_with_precedence(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("base instructions", encoding="utf-8")
    state = FactoryState(run_id="run-1", job="job", repository=str(tmp_path))
    store = RunStore(tmp_path)
    store.initialize(state)
    builder = ObservationBuilder(store, FactoryConfig())

    first = await builder.build(state)
    (tmp_path / "AGENTS.override.md").write_text("override instructions", encoding="utf-8")
    second = await builder.build(state)

    assert first.agents_md_path == "AGENTS.md"
    assert first.agents_md_instructions == "base instructions"
    assert second.agents_md_path == "AGENTS.override.md"
    assert second.agents_md_instructions == "override instructions"


@pytest.mark.asyncio
async def test_observation_uses_responsibility_owned_instruction_paths(tmp_path) -> None:
    (tmp_path / "PROJECT.md").write_text("project-specific instructions", encoding="utf-8")
    state = FactoryState(run_id="run-1", job="job", repository=str(tmp_path))
    store = RunStore(tmp_path)
    store.initialize(state)
    builder = ObservationBuilder(
        store,
        FactoryConfig(),
        repository_instruction_files=("PROJECT.override.md", "PROJECT.md"),
    )

    observation = await builder.build(state)

    assert observation.agents_md_path == "PROJECT.md"
    assert observation.agents_md_instructions == "project-specific instructions"


@pytest.mark.asyncio
async def test_observation_includes_non_secret_routing_bindings(tmp_path) -> None:
    state = FactoryState(
        run_id="run-1",
        job="job",
        repository=str(tmp_path),
        routing_bindings={"example.project": {"project_id": "project-123"}},
        active_extension_ids=["example"],
        extension_snapshot_revisions={"example": "revision-7"},
    )
    store = RunStore(tmp_path)
    store.initialize(state)

    observation = await ObservationBuilder(store, FactoryConfig()).build(state)

    assert observation.routing_bindings == {
        "example.project": {"project_id": "project-123"}
    }
    assert observation.active_extension_ids == ["example"]
    assert observation.extension_snapshot_revisions == {"example": "revision-7"}

from __future__ import annotations

import sys

import pytest
from pydantic import ValidationError

from foreman.config import CommandEvidenceConfig, FactoryConfig
from foreman.evidence import (
    DEFAULT_EVIDENCE_PROVIDERS,
    evidence_for,
    run_command_evidence,
    selected_command_evidence,
)
from foreman.extensions import load_foreman_file_config
from foreman.responsibilities import Check


@pytest.mark.asyncio
async def test_command_evidence_records_exit_and_bounded_output(tmp_path) -> None:
    provider = CommandEvidenceConfig(
        id="example",
        command=(
            sys.executable,
            "-c",
            "import sys; print('x' * 500); print('problem', file=sys.stderr); sys.exit(7)",
        ),
    )

    result = await run_command_evidence(
        provider,
        tmp_path,
        worker_id="worker-1",
        output_limit=100,
    )

    assert result.provider_id == "command.example"
    assert result.status == "completed"
    assert result.exit_code == 7
    assert result.stdout_tail.endswith("x" * 99 + "\n")
    assert "earlier bytes omitted" in result.stdout_tail
    assert result.stderr_tail == "problem\n"


@pytest.mark.asyncio
async def test_command_evidence_timeout_terminates_process(tmp_path) -> None:
    provider = CommandEvidenceConfig(
        id="slow",
        command=(sys.executable, "-c", "import time; time.sleep(30)"),
        timeout_seconds=0.05,
    )

    result = await run_command_evidence(
        provider,
        tmp_path,
        worker_id="worker-1",
        output_limit=100,
    )

    assert result.status == "timed_out"
    assert result.elapsed_seconds is not None and result.elapsed_seconds < 3


def test_central_config_loads_command_evidence(tmp_path) -> None:
    (tmp_path / "config.toml").write_text(
        """
[[evidence.commands]]
id = "pytest"
command = ["python", "-m", "pytest", "-q"]
timeout_seconds = 120
""".strip(),
        encoding="utf-8",
    )

    loaded = load_foreman_file_config(data_dir=tmp_path)

    assert loaded.evidence.commands == (
        CommandEvidenceConfig(
            id="pytest",
            command=("python", "-m", "pytest", "-q"),
            timeout_seconds=120,
        ),
    )


def test_check_without_evidence_uses_default_providers() -> None:
    check = Check("core.test", "default", "Judge it")

    assert evidence_for(check) == DEFAULT_EVIDENCE_PROVIDERS


def test_check_evidence_selects_only_referenced_commands() -> None:
    checks = (
        Check(
            "core.test",
            "custom",
            "Judge it",
            evidence=("git.diff", "command.pytest"),
        ),
    )
    configured = (
        CommandEvidenceConfig(id="pytest", command=("python", "-m", "pytest")),
        CommandEvidenceConfig(id="unused", command=("unused",)),
    )

    assert selected_command_evidence(checks, configured) == configured[:1]


def test_command_evidence_ids_must_be_unique() -> None:
    provider = CommandEvidenceConfig(id="tests", command=("pytest",))

    with pytest.raises(ValidationError, match="command evidence ids must be unique"):
        FactoryConfig(command_evidence=(provider, provider))


def test_command_is_an_argv_array_not_a_shell_string() -> None:
    with pytest.raises(ValidationError):
        CommandEvidenceConfig(id="unsafe", command="pytest; rm -rf target")

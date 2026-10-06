from importlib.metadata import version

from typer.testing import CliRunner

import foreman
from foreman.cli import app


def test_public_version_matches_distribution_metadata() -> None:
    assert foreman.__version__ == version("foreman-core")


def test_cli_version_requires_no_command_or_configuration(monkeypatch) -> None:
    monkeypatch.setenv("FOREMAN_CONFIG", "/does/not/exist")
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == "foreman-core " + foreman.__version__

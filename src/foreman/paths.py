from __future__ import annotations

import os
from pathlib import Path


def foreman_data_dir(configured: Path | str | None = None) -> Path:
    """Return Foreman's global, repository-independent data directory."""

    if configured is not None:
        return Path(configured).expanduser().resolve()
    environment = os.getenv("FOREMAN_DATA_DIR")
    if environment:
        return Path(environment).expanduser().resolve()
    return (Path.home() / ".foreman").resolve()


def foreman_config_path(
    data_dir: Path | str | None = None,
    configured: Path | str | None = None,
) -> Path:
    """Return the central Foreman configuration path."""

    if configured is not None:
        return Path(configured).expanduser().resolve()
    environment = os.getenv("FOREMAN_CONFIG")
    if environment:
        return Path(environment).expanduser().resolve()
    return foreman_data_dir(data_dir) / "config.toml"

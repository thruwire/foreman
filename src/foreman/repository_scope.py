from __future__ import annotations

import subprocess
from pathlib import Path


def _repository_identity(path: Path) -> Path:
    directory = path.expanduser().resolve()
    common = directory / ".git"
    if common.is_dir():
        return common.resolve()
    try:
        result = subprocess.run(
            [
                "git", "-C", str(directory), "rev-parse",
                "--path-format=absolute", "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return directory
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve()
    return directory


def repository_in_scope(repository: Path | str, repositories: tuple[str, ...] | None) -> bool:
    """Match exact repository identities, including subdirectories and linked Git worktrees."""
    if repositories is None:
        return True
    if not repositories:
        return False
    identity = _repository_identity(Path(repository))
    return any(identity == _repository_identity(Path(allowed)) for allowed in repositories)

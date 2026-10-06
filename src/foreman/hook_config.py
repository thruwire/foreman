from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from foreman.responsibilities import ResponsibilityFileConfig


class HookFileConfig(BaseModel):
    """Assistant-neutral controls for attached hooks, independent of explicit run jobs."""

    model_config = ConfigDict(extra="forbid")

    repositories: tuple[str, ...] | None = None
    responsibilities: dict[str, ResponsibilityFileConfig] = Field(default_factory=dict)

    @field_validator("repositories")
    @classmethod
    def absolute_repository_paths(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        paths: list[str] = []
        for item in value:
            path = Path(item).expanduser()
            if not item.strip() or not path.is_absolute():
                raise ValueError("hook repositories must be absolute repository paths")
            resolved = str(path.resolve())
            if resolved not in paths:
                paths.append(resolved)
        return tuple(paths)

    @field_validator("responsibilities")
    @classmethod
    def valid_responsibility_ids(
        cls, value: dict[str, ResponsibilityFileConfig]
    ) -> dict[str, ResponsibilityFileConfig]:
        invalid = sorted(
            key for key in value if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", key) is None
        )
        if invalid:
            raise ValueError("invalid responsibility ids: " + ", ".join(invalid))
        return value

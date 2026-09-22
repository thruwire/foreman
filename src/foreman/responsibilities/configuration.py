from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from foreman.config import FactoryConfig
from foreman.responsibilities.base import (
    Responsibility,
    ResponsibilityRegistry,
    ResponsibilityRoute,
)
from foreman.responsibilities.builtin import COMPLETION, VERIFICATION, builtin_registry

_REQUIRED_GLOBAL_RESPONSIBILITIES = {COMPLETION, VERIFICATION}
_RESPONSIBILITY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class ResponsibilityConfigError(ValueError):
    """A responsibility configuration file is malformed or cannot be applied."""


class ResponsibilityFileConfig(BaseModel):
    """Foreman-wide configuration owned by one responsibility."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    always: bool | None = None
    routing_instructions: str | None = None
    routing_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("routing_instructions")
    @classmethod
    def nonempty_routing_instructions(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("routing_instructions cannot be empty")
        return value

    def route(self, default: ResponsibilityRoute) -> ResponsibilityRoute:
        always = default.always if self.always is None else self.always
        instructions = (
            default.instructions if self.routing_instructions is None else self.routing_instructions
        )
        threshold = default.threshold if self.routing_threshold is None else self.routing_threshold
        return ResponsibilityRoute(
            always=always,
            instructions=instructions,
            threshold=threshold,
        )


def responsibility_config_dir() -> Path:
    """Return the packaged, Foreman-wide default responsibility directory."""

    return Path(__file__).resolve().parent / "definitions"


def _load_directory(directory: Path, *, required: bool) -> dict[str, ResponsibilityFileConfig]:
    if not directory.exists():
        if required:
            raise ResponsibilityConfigError(
                f"responsibility config directory does not exist: {directory}"
            )
        return {}
    if not directory.is_dir():
        raise ResponsibilityConfigError(
            f"responsibility config path is not a directory: {directory}"
        )

    configurations: dict[str, ResponsibilityFileConfig] = {}
    for path in sorted(directory.glob("*.toml")):
        responsibility_id = path.stem
        if _RESPONSIBILITY_ID.fullmatch(responsibility_id) is None:
            raise ResponsibilityConfigError(
                f"invalid responsibility id from config filename: {path.name}"
            )
        try:
            payload = tomllib.loads(path.read_text(encoding="utf-8"))
            configurations[responsibility_id] = ResponsibilityFileConfig.model_validate(payload)
        except (OSError, tomllib.TOMLDecodeError, ValidationError, ValueError) as error:
            raise ResponsibilityConfigError(
                f"invalid responsibility config {path}: {error}"
            ) from error
    return configurations


def _overlay_config(
    base: ResponsibilityFileConfig,
    override: ResponsibilityFileConfig,
) -> ResponsibilityFileConfig:
    payload = base.model_dump()
    changes = override.model_dump(exclude_unset=True)
    if "settings" in changes:
        changes["settings"] = {**base.settings, **changes["settings"]}
    return ResponsibilityFileConfig.model_validate({**payload, **changes})


def load_responsibility_configs(
    config_dir: Path | str | None = None,
) -> dict[str, ResponsibilityFileConfig]:
    """Load packaged defaults plus optional Foreman-wide external overrides."""

    defaults = _load_directory(responsibility_config_dir(), required=True)
    if config_dir is None:
        return defaults

    external = _load_directory(Path(config_dir).expanduser().resolve(), required=True)
    merged = dict(defaults)
    for responsibility_id, override in external.items():
        base = merged.get(responsibility_id)
        merged[responsibility_id] = override if base is None else _overlay_config(base, override)
    return merged


def configured_registry(
    factory_config: FactoryConfig,
    *,
    config_dir: Path | str | None = None,
    additional: Iterable[Responsibility] = (),
) -> ResponsibilityRegistry:
    """Build candidates from installed implementations and central Foreman configuration."""

    configurations = load_responsibility_configs(config_dir)
    try:
        builtins = builtin_registry(
            factory_config,
            settings={
                responsibility_id: config.settings
                for responsibility_id, config in configurations.items()
            },
        ).responsibilities
    except (ValidationError, ValueError) as error:
        raise ResponsibilityConfigError(f"invalid responsibility settings: {error}") from error

    configured_additional: list[Responsibility] = []
    for responsibility in additional:
        settings = configurations.get(responsibility.id, ResponsibilityFileConfig()).settings
        if settings:
            configure = getattr(responsibility, "configured", None)
            if not callable(configure):
                raise ResponsibilityConfigError(
                    f"settings for {responsibility.id} must be applied by its implementation"
                )
            try:
                responsibility = configure(settings)
            except (TypeError, ValueError) as error:
                raise ResponsibilityConfigError(
                    f"invalid settings for {responsibility.id}: {error}"
                ) from error
        configured_additional.append(responsibility)

    candidates = (*builtins, *configured_additional)
    candidate_ids = {responsibility.id for responsibility in candidates}
    unknown = set(configurations) - candidate_ids
    if unknown:
        raise ResponsibilityConfigError(
            "configuration has no installed responsibility implementation: "
            f"{', '.join(sorted(unknown))}"
        )

    for responsibility_id in _REQUIRED_GLOBAL_RESPONSIBILITIES:
        configured = configurations[responsibility_id]
        if not configured.enabled or configured.always is False:
            raise ResponsibilityConfigError(
                f"{responsibility_id} is a required global responsibility"
            )

    enabled = [
        responsibility
        for responsibility in candidates
        if configurations.get(responsibility.id, ResponsibilityFileConfig()).enabled
    ]
    try:
        routes = {
            responsibility.id: configurations.get(
                responsibility.id, ResponsibilityFileConfig()
            ).route(
                responsibility.route()
                if callable(getattr(responsibility, "route", None))
                else ResponsibilityRoute(always=True)
            )
            for responsibility in enabled
        }
        return ResponsibilityRegistry(enabled, routes=routes)
    except ValueError as error:
        raise ResponsibilityConfigError(f"invalid responsibility routing: {error}") from error

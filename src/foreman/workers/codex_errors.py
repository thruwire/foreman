"""Recognize terminal model-configuration errors from Codex transport errors."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

_MODEL_UNAVAILABLE = re.compile(
    r"(?:the\s+.+?\s+model\s+is\s+not\s+supported"
    r"|model\s+.+?\s+(?:does\s+not\s+exist|is\s+not\s+(?:supported|available))"
    r"|(?:do\s+not|don't)\s+have\s+access\s+to\s+(?:the\s+)?model)",
    re.IGNORECASE,
)


def model_configuration_error(error: Any, *, depth: int = 0) -> str | None:
    """Inspect provider errors, never arbitrary agent output, with bounded decoding."""
    if depth > 5:
        return None
    if isinstance(error, Mapping):
        code = error.get("code") or error.get("type")
        message = error.get("message")
        if isinstance(code, str) and code in {
            "model_not_found", "unsupported_model", "invalid_model",
        }:
            return _hint(str(message or code))
        for name in ("error", "message"):
            result = model_configuration_error(error.get(name), depth=depth + 1)
            if result:
                return result
    elif isinstance(error, str):
        text = error[:8000]
        if _MODEL_UNAVAILABLE.search(text):
            return _hint(text)
        start = text.find("{")
        if start >= 0:
            try:
                nested = json.loads(text[start:])
            except (ValueError, RecursionError):
                return None
            return model_configuration_error(nested, depth=depth + 1)
    return None


def _hint(message: str) -> str:
    return (
        f"Codex rejected the worker model: {message[:2000]}. "
        "Check the installed Codex version and account model access. "
        "Choose an available worker model with FOREMAN_CODEX_MODEL or native Codex configuration; "
        "FOREMAN_DECISION_MODEL controls only router/check decisions. No fallback was attempted."
    )

"""Record server-reported tokens and local sizes without recording request content."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from foreman.request_budget import BudgetedRequest

logger = logging.getLogger(__name__)


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _tokens(usage: Any, name: str) -> int | None:
    count = _field(usage, name)
    return count if type(count) is int and count >= 0 else None


class JevUsageRecorder:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path

    def record(
        self, response: Any, request: BudgetedRequest, *, purpose: str, model: str,
    ) -> None:
        usage = _field(response, "usage")
        returned_model = _field(response, "model")
        record = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "purpose": purpose,
            "model": returned_model if isinstance(returned_model, str) else model,
            "input_tokens": _tokens(usage, "input_tokens"),
            "output_tokens": _tokens(usage, "output_tokens"),
            "request_bytes_before": request.original_measurements["total_bytes"],
            "request_bytes_after": request.measurements["total_bytes"],
            "state_bytes": request.measurements["state_bytes"],
            "pair_bytes": request.measurements["pair_bytes"],
        }
        logger.info("Jev request usage: %s", record)
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                # One append write keeps concurrent hook records separate.
                payload = (json.dumps(record) + "\n").encode("utf-8")
                if os.write(descriptor, payload) != len(payload):
                    raise OSError("incomplete Jev usage write")
            finally:
                os.close(descriptor)
        except OSError:
            # Missing telemetry must not change a successful supervisory decision.
            logger.warning("Could not append Jev usage record to %s", self.path)

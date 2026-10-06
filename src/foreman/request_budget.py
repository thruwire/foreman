"""Conservative, dependency-free budgeting for Jev's two input limits.

The API does not expose its tokenizer. Count ASCII-escaped JSON bytes rather
than guessing tokens from characters. This deliberately overestimates normal
text and Unicode for byte-based tokenizers. Defaults leave headroom below the
published 32k pair / 64k request limits; this is not an exact token count.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class RequestBudgetError(RuntimeError):
    """Required input cannot fit without discarding decision-critical context."""


def json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode("ascii"))


def _text_candidates(
    value: Any, field: str, parent: Any, key: Any,
    candidates: list[tuple[int, str, Any, Any, str]],
) -> None:
    if isinstance(value, str) and len(value) > 256:
        candidates.append((json_bytes(value), field, parent, key, value))
    elif isinstance(value, dict):
        for child_key, child in value.items():
            _text_candidates(child, field, value, child_key, candidates)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _text_candidates(child, field, value, index, candidates)


# These fields must never be truncated by the request budget. Other evidence
# may be shortened, with explicit omission markers visible to the assessor.
PROTECTED_FIELDS = frozenset({
    "original_job", "work", "current_operation", "agents_md_path",
    "agents_md_instructions", "routing_bindings", "active_extension_ids",
    "extension_snapshot_revisions", "run_id", "factory_status", "iteration",
    "attempts", "elapsed_factory_seconds",
})


@dataclass(frozen=True)
class BudgetedRequest:
    state: dict[str, Any]
    measurements: dict[str, int]


@dataclass(frozen=True)
class JevRequestBudget:
    pair_bytes: int = 30_000
    total_bytes: int = 60_000

    def __post_init__(self) -> None:
        if not 512 <= self.pair_bytes <= 30_000 or not 512 <= self.total_bytes <= 60_000:
            raise ValueError("Jev byte budgets must be positive and below the model defaults")

    def measure(
        self, state: Mapping[str, Any], questions: Mapping[str, str],
    ) -> dict[str, int]:
        payloads = {
            key: {"type": "noul", "instructions": value, "criteria": None}
            for key, value in questions.items()
        }
        longest = max(payloads.values(), key=json_bytes, default={})
        return {
            "state_bytes": json_bytes(state),
            "pair_bytes": json_bytes({"state": state, "question": longest}),
            "total_bytes": json_bytes({"state": state, "questions": payloads}),
        }

    def _fits(self, sizes: Mapping[str, int]) -> bool:
        return sizes["pair_bytes"] <= self.pair_bytes and sizes["total_bytes"] <= self.total_bytes

    def fit(
        self, state: Mapping[str, Any], questions: Mapping[str, str], *, compact: bool = True,
    ) -> BudgetedRequest:
        result = copy.deepcopy(dict(state))
        original = self.measure(result, questions)
        if self._fits(original):
            return BudgetedRequest(result, original)

        omitted: set[str] = set()
        deduplicated: set[str] = set()

        def record() -> dict[str, int]:
            result["evidence_budget"] = {
                "method": "ascii_json_bytes",
                "original_state_bytes": original["state_bytes"],
                "omitted_fields": sorted(omitted),
                "deduplicated_fields": sorted(deduplicated),
                "notice": "Omitted evidence is unknown, not evidence of success or absence.",
            }
            return self.measure(result, questions)

        if compact:
            latest = result.get("latest_worker_output", "")
            seen: dict[str, str] = {latest.strip(): "latest_worker_output"} if latest else {}
            for field in ("active_workers", "worker_history"):
                for worker in result.get(field, []):
                    for stream in ("stdout_tail", "stderr_tail"):
                        value = worker.get(stream, "")
                        if value.strip() and value.strip() in seen:
                            worker[stream] = f"[duplicate of {seen[value.strip()]}]"
                            deduplicated.add(field)
                        elif value.strip():
                            seen[value.strip()] = f"{field}.{stream}"
            sizes = record()
            if self._fits(sizes):
                return BudgetedRequest(result, sizes)

            # Prefer recent evidence before reducing any current output or diff.
            for field in ("recent_events", "worker_history", "failures"):
                while len(result.get(field, [])) > 1:
                    result[field].pop(0)
                    omitted.add(field)
                    sizes = record()
                    if self._fits(sizes):
                        return BudgetedRequest(result, sizes)

            # Keep the head and tail of large optional strings, including the
            # newest tool result, diff, and command output. Preserve JSON shape.
            while True:
                candidates: list[tuple[int, str, Any, Any, str]] = []

                for field, value in result.items():
                    if field not in PROTECTED_FIELDS and field != "evidence_budget":
                        _text_candidates(value, field, result, field, candidates)
                if not candidates:
                    break
                _, field, parent, key, value = max(candidates, key=lambda item: item[0])
                keep = max(64, len(value) // 4)
                parent[key] = value[:keep] + "\n[... evidence omitted ...]\n" + value[-keep:]
                omitted.add(field)
                sizes = record()
                if self._fits(sizes):
                    return BudgetedRequest(result, sizes)

            # Even compact structures can grow large. Explicitly remove optional
            # fields as a last resort; the required state remains intact.
            optional = [
                field for field in result
                if field not in PROTECTED_FIELDS and field != "evidence_budget"
            ]
            for field in sorted(optional, key=lambda key: json_bytes(result[key]), reverse=True):
                del result[field]
                omitted.add(field)
                sizes = record()
                if self._fits(sizes):
                    return BudgetedRequest(result, sizes)

        sizes = self.measure(result, questions)
        raise RequestBudgetError(
            "Jev request budget exceeded: essential context and unchanged questions cannot fit "
            f"(state={sizes['state_bytes']} ASCII JSON bytes; "
            f"state + longest question={sizes['pair_bytes']}/{self.pair_bytes}; "
            f"whole request={sizes['total_bytes']}/{self.total_bytes}). "
            "Reduce the submitted work, tool arguments, instructions, or question definitions."
        )

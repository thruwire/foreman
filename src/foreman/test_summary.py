from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

TestOutcomeStatus = Literal["passed", "failed", "errored", "unknown"]

_SUMMARY_LINE_RE = re.compile(
    r"^(?:=+\s*)?"
    r"(?P<parts>(?:\d+\s+[A-Za-z]+)(?:\s*,\s*\d+\s+[A-Za-z]+)*)"
    r"(?:\s+in\s+[\d.]+s)?"
    r"(?:\s*=+)?$"
)
_COUNT_RE = re.compile(r"(\d+)\s+([A-Za-z]+)")


@dataclass(frozen=True)
class PytestSummary:
    """Structured outcome parsed from one pytest summary line."""

    passed: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0
    line: str = ""

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errored + self.skipped

    @property
    def status(self) -> TestOutcomeStatus:
        """Overall status: errors dominate failures, either dominates a pass."""
        if self.errored:
            return "errored"
        if self.failed:
            return "failed"
        if self.total:
            return "passed"
        return "unknown"

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "failed": self.failed,
            "errored": self.errored,
            "skipped": self.skipped,
            "total": self.total,
            "status": self.status,
            "summary": self.line,
        }


def parse_pytest_summary_line(line: str) -> PytestSummary | None:
    """Parse one pytest summary line into a structured outcome.

    Handles both passed-first and failure-first orderings ("1 failed, 3
    passed"), the "N error"/"N errors" forms, and skipped counts, with or
    without the trailing "in Ns" timing and the "=== ... ===" wrappers pytest
    prints. Returns None for lines that are not test summaries, or for
    summaries with no recognized test counts (e.g. "2 warnings in 0.1s").
    """
    text = line.strip()
    if not text:
        return None
    match = _SUMMARY_LINE_RE.match(text)
    if not match:
        return None
    passed = failed = errored = skipped = 0
    for count_text, word in _COUNT_RE.findall(match.group("parts")):
        count = int(count_text)
        normalized = word.lower()
        if normalized == "passed":
            passed += count
        elif normalized == "failed":
            failed += count
        elif normalized in ("error", "errors"):
            errored += count
        elif normalized == "skipped":
            skipped += count
    if not (passed or failed or errored or skipped):
        return None
    return PytestSummary(
        passed=passed,
        failed=failed,
        errored=errored,
        skipped=skipped,
        line=text[:200],
    )


def parse_pytest_summaries(output: str) -> list[PytestSummary]:
    """Extract every pytest summary line from worker output, in order."""
    summaries: list[PytestSummary] = []
    for line in output.splitlines():
        summary = parse_pytest_summary_line(line)
        if summary is not None:
            summaries.append(summary)
    return summaries

"""Fix the regex: pytest summary is 'N failed, M passed in Xs' — failed comes
BEFORE passed. Rewrite the parser to handle both orders and require 'in Xs'
suffix (the junk line lacks it)."""

src = open("src/foreman/observation.py").read()
old = src[src.find("_PYTEST_LINE = re.compile"):src.find("def _pytest_summary_from_output")]
new = '''_PYTEST_LINE = re.compile(
    r"^(?:=+\\s*)?"
    r"(?P<parts>(?:(?:\\d+\\s+\\w+)(?:,\\s*)?)+)"
    r"\\s+in\\s+(?P<time>[\\d.]+)s"
    "(?:\\\\s*=+)?\\\\s*$"
)


def _pytest_summary_from_output(output: str) -> list[dict[str, Any]]:
    """Extract pytest summary lines from worker output into test_results
    entries so assessments can key on executed test evidence even when the
    backend streams no structured events (hermes buffers NDJSON on Windows).
    Handles 'N passed', 'N failed, M passed', 'N error' orderings.
    """
    results: list[dict[str, Any]] = []
    for line in output.splitlines():
        match = _PYTEST_LINE.match(line.strip())
        if not match:
            continue
        parts = {m.group(2).lower(): int(m.group(1)) for m in re.finditer(r"(\\d+)\\s+(\\w+)", match.group("parts"))}
        if not parts:
            continue
        results.append(
            {
                "source": "worker_output",
                "passed": parts.get("passed", 0),
                "failed": parts.get("failed", 0) + parts.get("error", 0),
                "summary": line.strip()[:200],
            }
        )
    return results

'''
src = src.replace(old, new)
open("src/foreman/observation.py", "w").write(src)
print("parser rewritten")

# fix the test expectation for junk line (still shouldn't match: no 'in Xs')
r = __import__("subprocess").run(
    [".venv/Scripts/python", "-m", "pytest", "tests/test_observation_evidence.py", "-q", "-p", "no:cacheprovider"],
    capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
)
print(r.stdout[-400:])
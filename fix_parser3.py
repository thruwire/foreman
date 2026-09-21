"""The old function body is still there after the new one (my replace only
swapped the header+regex, leaving a duplicate function def). Consolidate:
keep ONE _pytest_summary_from_output with the fixed body. Rewrite the whole
region cleanly with python."""

src = open("src/foreman/observation.py").read()
start = src.find("_PYTEST_LINE = re.compile")
# find the END of the duplicated function: the second 'def _pytest_summary_from_output' + its body end
# Simplest: find the marker 'return results' second occurrence end
first_def = src.find("def _pytest_summary_from_output")
second_def = src.find("def _pytest_summary_from_output", first_def + 10)
# find end of second function: next 'def ' or 'class ' at module level after second_def
import re as _re
m = _re.search(r"\n(def |class )", src[second_def + 10:])
end = second_def + 10 + m.start() if m else len(src)
# remove from first _PYTEST_LINE def through end of second function, then insert one clean version
new_block = '''_PYTEST_LINE = re.compile(
    r"^(?:=+\\\\s*)?"
    r"(?P<parts>(?:(?:\\\\d+\\\\s+\\\\w+)(?:,\\\\s*)?)+)"
    r"\\\\s+in\\\\s+(?P<time>[\\\\d.]+)s"
    r"(?:\\\\s*=+)?\\\\s*$"
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
        parts = {
            word.lower(): int(count)
            for count, word in re.findall(r"(\\d+)\\s+([A-Za-z]+)", match.group("parts"))
        }
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
src = src[:start] + new_block + src[end:]
open("src/foreman/observation.py", "w").write(src)
r = __import__("subprocess").run(
    [".venv/Scripts/python", "-m", "pytest", "tests/test_observation_evidence.py", "-q", "-p", "no:cacheprovider"],
    capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
)
print(r.stdout[-300:])
"""Add a unit test for untracked_evidence, then calibration fix 2:
TEST_RESULT evidence from worker output. When a worker's stdout contains
pytest-style results ('N passed'), foreman's test_results list is empty —
Jev's tests_sufficient has nothing to key on. Parse pytest lines from worker
stdout into test_results entries in the observation."""
src = open("src/foreman/observation.py").read()

if "PYTEST_LINE" not in src:
    src = src.replace(
        "import asyncio\nfrom datetime import UTC, datetime",
        "import asyncio\nimport re\nfrom datetime import UTC, datetime",
    )
    helper = '''

_PYTEST_LINE = re.compile(
    r"^(?:=+\\s*)?(?P<count>\\d+)\\s+passed(?P<failed>.*?)(?:\\s+in\\s+(?P<time>[\\d.]+)s)?(?:\\s*=+)?\\s*$"
)


def _pytest_summary_from_output(output: str) -> list[dict[str, Any]]:
    """Extract pytest summary lines from worker output into test_results
    entries so assessments can key on executed test evidence even when the
    backend streams no structured events (hermes buffers NDJSON on Windows).
    """
    results: list[dict[str, Any]] = []
    for line in output.splitlines():
        match = _PYTEST_LINE.match(line.strip())
        if match:
            passed = int(match.group("count"))
            failed_text = (match.group("failed") or "").strip()
            failed = 0
            failed_match = re.search(r"(\\d+)\\s+failed", failed_text)
            if failed_match:
                failed = int(failed_match.group(1))
            results.append(
                {
                    "source": "worker_output",
                    "passed": passed,
                    "failed": failed,
                    "summary": line.strip()[:200],
                }
            )
    return results

'''
    src = src.replace("\n\nclass ObservationBuilder:", helper + "\n\nclass ObservationBuilder:", 1)
    src = src.replace(
        "            test_results=[],",
        "            test_results=_pytest_summary_from_output(\n"
        "                (latest.stdout or \"\") + \"\\n\" + (latest.stderr or \"\")\n"
        "            )\n"
        "            if latest\n"
        "            else [],",
    )
    open("src/foreman/observation.py", "w").write(src)
    print("pytest parser added")
else:
    print("already present")
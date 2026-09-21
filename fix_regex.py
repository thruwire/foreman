"""The written regex has DOUBLED backslashes (\\\\s in the file = literal
backslash-s in the pattern). My fix_parser3 wrote it via a non-raw string.
Fix by rewriting the pattern lines with a raw string."""

src = open("src/foreman/observation.py").read()
bad = src[src.find("_PYTEST_LINE = re.compile"):src.find("\n\n\ndef _pytest_summary_from_output")]
fixed = (
    '_PYTEST_LINE = re.compile(\n'
    '    r"^(?:=+\\s*)?"\n'
    '    r"(?P<parts>(?:(?:\\d+\\s+\\w+)(?:,\\s*)?)+)"\n'
    '    r"\\s+in\\s+(?P<time>[\\d.]+)s"\n'
    '    r"(?:\\s*=+)?\\s*$"\n'
    ')'
)
src = src.replace(bad, fixed)
open("src/foreman/observation.py", "w").write(src)

r = __import__("subprocess").run(
    [".venv/Scripts/python", "-m", "pytest", "tests/test_observation_evidence.py", "-q", "-p", "no:cacheprovider"],
    capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
)
print(r.stdout[-300:])
"""Group numbering bug: m.group(2) doesn't exist — the inner finditer groups
are separate. Use named groups in the parts pattern or simpler non-capturing
outer. Fix by finding all 'N word' pairs from the matched segment."""
src = open("src/foreman/observation.py").read()
src = src.replace(
    '        parts = {m.group(2).lower(): int(m.group(1)) for m in re.finditer(r"(\\d+)\\s+(\\w+)", match.group("parts"))}',
    '        parts = {\n'
    '            word.lower(): int(count)\n'
    '            for count, word in re.findall(r"(\\d+)\\s+([A-Za-z]+)", match.group("parts"))\n'
    '        }',
)
open("src/foreman/observation.py", "w").write(src)
r = __import__("subprocess").run(
    [".venv/Scripts/python", "-m", "pytest", "tests/test_observation_evidence.py", "-q", "-p", "no:cacheprovider"],
    capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
)
print(r.stdout[-300:])
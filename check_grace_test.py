"""Check the collection error in test_policy_grace.py."""
import subprocess

r = subprocess.run(
    [".venv/Scripts/python", "-m", "pytest", "tests/test_policy_grace.py", "-p", "no:cacheprovider"],
    capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
)
print(r.stdout[-1200:])
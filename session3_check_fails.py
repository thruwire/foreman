"""3rd failure: probably the drift test again (flaky under load) or the new
hook. Identify it + check the drift test specifically."""
import subprocess

r = subprocess.run(
    [".venv/Scripts/python", "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider"],
    capture_output=True, text=True, timeout=300, encoding="utf-8", errors="replace",
)
fails = [line for line in r.stdout.splitlines() if line.startswith("FAILED")]
print("\n".join(fails))
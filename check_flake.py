"""The coalesce test is flaky (passed on rerun — timing-dependent, likely a
pre-existing flake under parallel load). The drift/steer failure is the
known Windows one. Verify the grace patch works: unit test for the grace
branch, then commit."""
import subprocess

r = subprocess.run(
    [".venv/Scripts/python", "-m", "pytest",
     "tests/test_integration.py::test_noisy_events_are_coalesced",
     "tests/test_integration.py::test_noisy_events_are_coalesced",
     "tests/test_integration.py::test_noisy_events_are_coalesced",
     "-q", "-p", "no:cacheprovider"],
    capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace",
)
print(r.stdout[-500:])
"""Check the new test_noisy_events_are_coalesced failure — is it caused by
the grace patch (policy now returns CONTINUE during grace, changing event
ordering) or pre-existing? Look at the failure detail."""
import subprocess

r = subprocess.run(
    [".venv/Scripts/python", "-m", "pytest",
     "tests/test_integration.py::test_noisy_events_are_coalesced", "-p", "no:cacheprovider"],
    capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
)
print(r.stdout[-1800:])
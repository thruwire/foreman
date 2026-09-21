"""Calibration fix candidates for the hermes evidence gap.

Fix 1: `git diff` misses UNTRACKED files (test_calc.py is `??`). Jev never
sees the test file content -> tests_sufficient stays ~0.05. Add
`git diff` + intent-to-add trick: `git add -N .` makes untracked files
appear in diff. Do it in the observation builder? No — mutating the repo
from an observer is wrong. Better: capture untracked file contents
separately into the observation via git status --porcelain parsing +
reading the files (bounded).

Simpler: add a `git ls-files --others --exclude-standard` list to
git_status evidence so Jev at least sees untracked file NAMES, and raise
the per-line cap. Let me check how git_status flows into the observation.
"""
import subprocess

r = subprocess.run(
    ["grep", "-n", "git_status\|test_results", "C:/repos/foreman/src/foreman/foreman/jev.py"],
    capture_output=True, text=True, timeout=30,
)
print(r.stdout)
r2 = subprocess.run(
    ["sed", "-n", "121,160p", "C:/repos/foreman/src/foreman/foreman/jev.py"],
    capture_output=True, text=True, timeout=30,
)
print(r2.stdout)
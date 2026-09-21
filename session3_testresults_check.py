"""Run 002eae84 shows the problem clearly now: with alpha=0.25 the tests
score is dragged DOWN by the heavy smoothing (tests raw oscillates 0.13-0.17
late in the run — Jev is scoring the LATER workers' output as low-test, and
the smoothed value stays ~0.14, way below the 0.30 bar).

The fundamental issue: Jev's tests_sufficient for hermes evidence is
GENUINELY low (it doesn't credit buffered output as test evidence), not
noisy. Smoothing can't fix a biased estimator. The score oscillates in SOME
runs (impl 0.75) but tests is consistently low — Jev does not believe tests
ran.

The real fix must be in the observation: test_results parsing (added
earlier) populates observation.test_results — but only from the LATEST
worker's stdout. If the latest worker didn't run pytest (e.g. it just
verified files), test_results is empty.

Better evidence: after each worker completes, foreman could run its OWN
quick pytest in the repo and record real TEST_RESULT events. That's the
`test_results` field's intended purpose! Check how test_results is
populated in runtime — is there a hook for foreman-run tests?"""
import subprocess

PS = (
    "Invoke-Command -ComputerName mraize -ScriptBlock { "
    "cd C:\\Users\\tjarman\\repos\\foreman; "
    "Select-String -Path src\\foreman\\runtime.py -Pattern 'test_results|TEST_RESULT|pytest' | "
    "Select-Object -First 8 | ForEach-Object { $_.LineNumber.ToString() + ': ' + $_.Line.Trim().Substring(0, [Math]::Min(110, $_.Line.Trim().Length)) } }"
)
r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", PS],
                   capture_output=True, text=True, timeout=120,
                   encoding="utf-8", errors="replace")
print(r.stdout)
if r.stderr:
    print("STDERR:", (r.stderr or "")[-150:])
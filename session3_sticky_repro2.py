"""Now I see it precisely: the SMOOTHED finish converges to 0.28 (equals the
threshold) but the smoothed finish_ready check uses `>=` on all three
smoothed values simultaneously: smoothed finish 0.28 >= 0.28 ✓, tests 0.34
>= 0.30 ✓, req 0.42 >= 0.30 ✓ — all three pass on it9! But sticky stayed
False... because finish_ready uses the assessment AFTER smoothing mutation?
The EMA update mutates the ASSESSMENT fields (setattr assessment name
smoothed). Then finish_ready reads assessment.ready_to_finish (now
smoothed 0.28) >= 0.28 ✓...

Hmm wait, it9 smoothed finish = 0.284? The print shows 0.28 (rounded).
If actual is 0.2799 < 0.28, no cross. The oscillation damping with
alpha=0.4 converges to ~0.28 which EQUALS the threshold — borderline.

The problem: with alpha=0.4 the EMA is dominated by the raw values (40%
new), oscillation persists (0.587 dip in the impl trace). For reliable
FINISH, use a LOWER alpha (0.25) for heavier smoothing, or lower the
finish threshold to 0.26.

Given the empirical convergence to ~0.28, the calibrated threshold 0.25
with alpha=0.4 gives comfortable margin. This is the last knob turn: run
with finish_threshold=0.25 (tests 0.30, req 0.30 unchanged)."""
import base64
import subprocess

pilot = open("C:/repos/foreman/run_hermes_pilot.py").read().replace(
    "finish_threshold=0.28,", "finish_threshold=0.25,",
).replace(
    "score_smoothing_alpha=0.4,", "score_smoothing_alpha=0.25,",
)
b64 = base64.b64encode(pilot.encode()).decode()

PS = (
    "Invoke-Command -ComputerName mraize -ScriptBlock { "
    f"[System.IO.File]::WriteAllBytes('C:/Users/tjarman/repos/foreman/run_hermes_pilot.py', "
    f"[Convert]::FromBase64String('{b64}')); "
    "cd C:\\Users\\tjarman\\repos\\foreman-scratch; "
    "git reset --hard 00fe7d5 2>&1 | Out-Null; "
    "Remove-Item -Recurse -Force __pycache__ -EA SilentlyContinue; "
    "Remove-Item -Recurse -Force .foreman -EA SilentlyContinue; "
    "cd C:\\Users\\tjarman\\repos\\foreman; "
    "$out = & .venv\\Scripts\\python.exe run_hermes_pilot.py 2>&1; "
    "Write-Output ($out | Select-Object -Last 12) }"
)
r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", PS],
                   capture_output=True, text=True, timeout=590,
                   encoding="utf-8", errors="replace")
print(r.stdout[-2600:])
if r.stderr:
    print("STDERR:", (r.stderr or "")[-250:])
"""Found the mismatch: the pilot on mraize has max_workers=5 and NO smoothing
lines — my session3_transfer2 wrote the pilot from cord's copy BEFORE the
replace chain applied correctly? No: transfer2 replaced 'max_workers=6,' —
but cord's run_hermes_pilot.py has max_workers=3 (I reverted it in a later
edit). The replace silently did nothing, and the pushed file lacked both
max_workers=6 AND the smoothing lines.

Fix: regenerate the pilot with explicit replacements against the ACTUAL
content, verify locally, then push."""
import subprocess

pilot = open("C:/repos/foreman/run_hermes_pilot.py").read()
pilot = pilot.replace(
    "        max_workers=3,",
    "        max_workers=6,\n"
    "        use_smoothed_scores=True,\n"
    "        score_smoothing_alpha=0.4,",
)
assert "use_smoothed_scores=True" in pilot, "replace failed"
assert "max_workers=6," in pilot
open("C:/repos/foreman/run_hermes_pilot.py", "w").write(pilot)
import base64
pb64 = base64.b64encode(pilot.encode()).decode()

PS = (
    "Invoke-Command -ComputerName mraize -ScriptBlock { "
    f"[System.IO.File]::WriteAllBytes('C:/Users/tjarman/repos/foreman/run_hermes_pilot.py', "
    f"[Convert]::FromBase64String('{pb64}')); "
    "Select-String -Path 'C:/Users/tjarman/repos/foreman/run_hermes_pilot.py' -Pattern 'smoothed|max_workers' | "
    "ForEach-Object { $_.Line.Trim() }; "
    "cd C:\\Users\\tjarman\\repos\\foreman-scratch; "
    "git reset --hard ed8f095 2>&1 | Out-Null; "
    "Remove-Item -Recurse -Force __pycache__ -EA SilentlyContinue; "
    "Remove-Item -Recurse -Force .foreman -EA SilentlyContinue; "
    "git log --oneline -1; "
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
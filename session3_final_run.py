"""Final calibrated run: smoothing on, finish_threshold=0.28 (matching the
observed smoothed plateau for genuinely-complete jobs)."""
import base64
import subprocess

pilot = open("C:/repos/foreman/run_hermes_pilot.py").read().replace(
    "finish_threshold=0.35,", "finish_threshold=0.28,",
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
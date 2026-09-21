"""Rewrite run_hermes_pilot.py with all calibrated settings baked in."""
import base64

pilot = open("C:/repos/foreman/run_hermes_pilot.py").read()
pilot = pilot.replace(
    "        max_workers=3,",
    "        max_workers=5,\n"
    "        cold_start_grace_seconds=30.0,\n"
    "        implementation_for_verification_threshold=0.45,\n"
    "        assessment_min_interval_seconds=15,",
).replace("        assessment_min_interval_seconds=5,\n", "")
open("C:/repos/foreman/run_hermes_pilot.py", "w").write(pilot)

print("max_workers=5:", "max_workers=5," in pilot)
print("grace:", "cold_start_grace_seconds=30.0," in pilot)
print("verifier threshold:", "implementation_for_verification_threshold=0.45," in pilot)
print("interval 15:", "assessment_min_interval_seconds=15," in pilot)
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
r = __import__("subprocess").run(["powershell.exe", "-NoProfile", "-Command", PS],
                   capture_output=True, text=True, timeout=590,
                   encoding="utf-8", errors="replace")
print(r.stdout[-2600:])
if r.stderr:
    print("STDERR:", (r.stderr or "")[-250:])
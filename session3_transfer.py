"""Clean session-3 helper scripts, transfer the EMA commit to mraize, run
the pilot with smoothing enabled."""
import subprocess
import base64

import os
for f in ["session3_step1.py", "session3_step2.py", "session3_step3.py",
          "session3_step4.py", "session3_step5.py"]:
    try:
        os.remove(f)
    except OSError:
        pass

subprocess.run(
    ["git", "format-patch", "-1", "HEAD", "--stdout"],
    stdout=open("C:/Users/tjarman/AppData/Local/Temp/foreman-ema.patch", "wb"),
    check=True,
)
b64 = base64.b64encode(open("C:/Users/tjarman/AppData/Local/Temp/foreman-ema.patch", "rb").read()).decode()

# calibrated pilot with smoothing enabled
pilot = open("C:/repos/foreman/run_hermes_pilot.py").read().replace(
    "        max_workers=6,",
    "        max_workers=6,\n"
    "        use_smoothed_scores=True,\n"
    "        score_smoothing_alpha=0.4,",
)
pb64 = base64.b64encode(pilot.encode()).decode()

PS = (
    "Invoke-Command -ComputerName mraize -ScriptBlock { "
    f"[System.IO.File]::WriteAllBytes('C:/Users/tjarman/AppData/Local/Temp/foreman-ema.patch', "
    f"[Convert]::FromBase64String('{b64}')); "
    "cd C:\\Users\\tjarman\\repos\\foreman; "
    "git am C:/Users/tjarman/AppData/Local/Temp/foreman-ema.patch 2>&1 | Select-Object -First 1; "
    f"[System.IO.File]::WriteAllBytes('C:/Users/tjarman/repos/foreman/run_hermes_pilot.py', "
    f"[Convert]::FromBase64String('{pb64}')); "
    "$t = & .venv\\Scripts\\python.exe -m pytest tests/test_policy_ema.py -q -p no:cacheprovider 2>&1; "
    "Write-Output ($t | Select-Object -Last 1); "
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
print(r.stdout[-2800:])
if r.stderr:
    print("STDERR:", (r.stderr or "")[-250:])
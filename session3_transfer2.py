"""Command too long (patch+embedded pilot b64 in one command). Chunk the
EMA patch transfer separately, then push the pilot file separately."""
import subprocess
import base64

subprocess.run(
    ["git", "format-patch", "-1", "HEAD", "--stdout"],
    stdout=open("C:/Users/tjarman/AppData/Local/Temp/foreman-ema.patch", "wb"),
    check=True,
)
b64 = base64.b64encode(open("C:/Users/tjarman/AppData/Local/Temp/foreman-ema.patch", "rb").read()).decode()
CHUNK = 14000
chunks = [b64[i:i+CHUNK] for i in range(0, len(b64), CHUNK)]
for i, chunk in enumerate(chunks):
    mode = "WriteAllText" if i == 0 else "AppendAllText"
    ps = (
        "Invoke-Command -ComputerName mraize -ScriptBlock { "
        f"[System.IO.File]::{mode}('C:/Users/tjarman/AppData/Local/Temp/foreman-ema.b64', '{chunk}'); "
        f"Write-Output 'c{i} ok' }}"
    )
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
    if f"c{i} ok" not in r.stdout:
        print(f"chunk {i} FAILED"); raise SystemExit(1)
print(f"patch transferred ({len(chunks)} chunks)")

pilot = open("C:/repos/foreman/run_hermes_pilot.py").read().replace(
    "        max_workers=6,",
    "        max_workers=6,\n"
    "        use_smoothed_scores=True,\n"
    "        score_smoothing_alpha=0.4,",
)
pb64 = base64.b64encode(pilot.encode()).decode()
PS = (
    "Invoke-Command -ComputerName mraize -ScriptBlock { "
    "[System.IO.File]::WriteAllBytes('C:/Users/tjarman/AppData/Local/Temp/foreman-ema.patch', "
    "[Convert]::FromBase64String([System.IO.File]::ReadAllText('C:/Users/tjarman/AppData/Local/Temp/foreman-ema.b64'))); "
    f"[System.IO.File]::WriteAllBytes('C:/Users/tjarman/repos/foreman/run_hermes_pilot.py', "
    f"[Convert]::FromBase64String('{pb64}')); "
    "cd C:\\Users\\tjarman\\repos\\foreman; "
    "git am C:/Users/tjarman/AppData/Local/Temp/foreman-ema.patch 2>&1 | Select-Object -First 1; "
    "Write-Output ('HEAD: ' + (git log --oneline -1)); "
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
"""The policy code is fine. The state.json shows the flag False at the END —
meaning either the runtime serialized state BEFORE policy set it, or it
round-trips state from disk each iteration (fresh object). Check the
runtime's persistence pattern: does it re-create state from store.load?"""
import subprocess

PS = (
    "Invoke-Command -ComputerName mraize -ScriptBlock { "
    "cd C:\\Users\\tjarman\\repos\\foreman; "
    "Select-String -Path src\\foreman\\runtime.py -Pattern 'self.state = |store.save|persist' | "
    "Select-Object -First 10 | ForEach-Object { $_.LineNumber.ToString() + ': ' + $_.Line.Trim().Substring(0, [Math]::Min(100, $_.Line.Trim().Length)) } }"
)
r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", PS],
                   capture_output=True, text=True, timeout=120,
                   encoding="utf-8", errors="replace")
print(r.stdout)
if r.stderr:
    print("STDERR:", (r.stderr or "")[-150:])
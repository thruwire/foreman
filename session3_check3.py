"""Code is live on mraize (defaults alpha=1.0, use=False — correct). The
pilot passes use_smoothed_scores=True explicitly... unless the pilot script
on mraize was overwritten by the LAST transfer (which had smoothing). Check
the actual pilot file content on mraize for the smoothed flags."""
import subprocess

PS = (
    "Invoke-Command -ComputerName mraize -ScriptBlock { "
    "Select-String -Path 'C:\\Users\\tjarman\\repos\\foreman\\run_hermes_pilot.py' -Pattern 'smoothed|max_workers|alpha' | "
    "ForEach-Object { $_.LineNumber.ToString() + ': ' + $_.Line.Trim() } }"
)
r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", PS],
                   capture_output=True, text=True, timeout=120,
                   encoding="utf-8", errors="replace")
print(r.stdout)
if r.stderr:
    print("STDERR:", (r.stderr or "")[-150:])
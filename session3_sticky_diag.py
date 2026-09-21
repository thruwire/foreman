"""Code is correct on cord. On mraize: is the arm line present in the
DEPLOYED policy.py? The raw-file push of observation.py earlier replaced
the whole file, but the sticky commit was applied via git am — should be
present. Verify the deployed policy.py has the arm line and check the
state.json was actually written AFTER the final assessment (maybe the
state.json save happens before the last policy decision)."""
import subprocess

PS = (
    "Invoke-Command -ComputerName mraize -ScriptBlock { "
    "cd C:\\Users\\tjarman\\repos\\foreman; "
    "Select-String -Path src\\foreman\\policy.py -Pattern 'finish_thresholds_met = True' | "
    "ForEach-Object { $_.LineNumber.ToString() + ': ' + $_.Line.Trim() }; "
    "$p = 'C:\\Users\\tjarman\\repos\\foreman-scratch\\.foreman\\runs\\7b74a56d1746\\state.json'; "
    "(Get-Item $p).LastWriteTime.ToString('HH:mm:ss'); "
    "$log = Select-String -Path 'C:\\Users\\tjarman\\repos\\foreman-scratch\\.foreman\\runs\\7b74a56d1746\\events.jsonl' "
    "-Pattern 'FACTORY_ESCALATED' | ForEach-Object { ($_.Line | ConvertFrom-Json).timestamp } "
    "Write-Output ('escalated at: ' + $log) }"
)
r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", PS],
                   capture_output=True, text=True, timeout=120,
                   encoding="utf-8", errors="replace")
print(r.stdout)
if r.stderr:
    print("STDERR:", (r.stderr or "")[-150:])
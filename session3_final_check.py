"""Check the 0.28-threshold run: smoothed finish over time vs the 0.28 bar.
The verifier ran (worker-3). Did sticky fire?"""
import subprocess

PS = (
    "Invoke-Command -ComputerName mraize -ScriptBlock { "
    "$p = 'C:\\Users\\tjarman\\repos\\foreman-scratch\\.foreman\\runs\\dacd8e6efe53\\state.json'; "
    "$s = Get-Content $p -Raw | ConvertFrom-Json; "
    "Write-Output ('sticky: ' + $s.finish_thresholds_met + ' | smoothed impl: ' + [math]::Round($s.smoothed_scores.implementation_complete, 2) + ' tests: ' + [math]::Round($s.smoothed_scores.tests_sufficient, 2) + ' finish: ' + [math]::Round($s.smoothed_scores.ready_to_finish, 2)); "
    "$p2 = 'C:\\Users\\tjarman\\repos\\foreman-scratch\\.foreman\\runs\\dacd8e6efe53\\events.jsonl'; "
    "$ev = Select-String -Path $p2 -Pattern 'FOREMAN_ASSESSED' | ForEach-Object { ConvertFrom-Json $_.Line }; "
    "$ev | ForEach-Object { "
    "$a = $_.payload.assessment; "
    "Write-Output ('it{0} impl={1:N2} tests={2:N2} finish={3:N2}' -f "
    "$_.payload.iteration, $a.implementation_complete, $a.tests_sufficient, $a.ready_to_finish) } }"
)
r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", PS],
                   capture_output=True, text=True, timeout=150,
                   encoding="utf-8", errors="replace")
print(r.stdout[-2200:])
if r.stderr:
    print("STDERR:", (r.stderr or "")[-150:])
"""Smoothing is now active on mraize (6 workers, verifier ran). Check the
smoothed timeline: did smoothed scores converge, and how close did finish
get? Also check alpha=0.4 smoothing effect on the noise."""
import subprocess

PS = (
    "Invoke-Command -ComputerName mraize -ScriptBlock { "
    "$p = 'C:\\Users\\tjarman\\repos\\foreman-scratch\\.foreman\\runs\\2d7ef833a870\\state.json'; "
    "$s = Get-Content $p -Raw | ConvertFrom-Json; "
    "Write-Output ('smoothed impl: ' + [math]::Round($s.smoothed_scores.implementation_complete, 2)); "
    "Write-Output ('smoothed tests: ' + [math]::Round($s.smoothed_scores.tests_sufficient, 2)); "
    "Write-Output ('smoothed finish: ' + [math]::Round($s.smoothed_scores.ready_to_finish, 2)); "
    "Write-Output ('sticky: ' + $s.finish_thresholds_met); "
    "$p2 = 'C:\\Users\\tjarman\\repos\\foreman-scratch\\.foreman\\runs\\2d7ef833a870\\events.jsonl'; "
    "$ev = Select-String -Path $p2 -Pattern 'FOREMAN_ASSESSED' | ForEach-Object { ConvertFrom-Json $_.Line }; "
    "$ev | ForEach-Object { "
    "$a = $_.payload.assessment; "
    "Write-Output ('it{0} impl={1:N2} tests={2:N2} finish={3:N2}' -f "
    "$_.payload.iteration, $a.implementation_complete, $a.tests_sufficient, $a.ready_to_finish) } }"
)
r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", PS],
                   capture_output=True, text=True, timeout=150,
                   encoding="utf-8", errors="replace")
print(r.stdout[-2600:])
if r.stderr:
    print("STDERR:", (r.stderr or "")[-150:])
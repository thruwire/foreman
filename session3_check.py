"""Still escalated — but the pilot on mraize must have smoothing active.
Wait: the pilot script sets use_smoothed_scores=True in FactoryConfig, but
the policy reads self.config.use_smoothed_scores — confirm the pilot on
mraize actually has it, and pull the smoothed timeline from state.json to
see convergence behavior."""
import subprocess

PS = (
    "Invoke-Command -ComputerName mraize -ScriptBlock { "
    "Select-String -Path 'C:\\Users\\tjarman\\repos\\foreman\\run_hermes_pilot.py' -Pattern 'smoothed' | "
    "ForEach-Object { $_.Line.Trim() }; "
    "$p = 'C:\\Users\\tjarman\\repos\\foreman-scratch\\.foreman\\runs\\48b8aee8347a\\state.json'; "
    "$s = Get-Content $p -Raw | ConvertFrom-Json; "
    "Write-Output ('smoothed impl: ' + [math]::Round($s.smoothed_scores.implementation_complete, 2)); "
    "Write-Output ('smoothed tests: ' + [math]::Round($s.smoothed_scores.tests_sufficient, 2)); "
    "Write-Output ('smoothed finish: ' + [math]::Round($s.smoothed_scores.ready_to_finish, 2)); "
    "Write-Output ('finish_thresholds_met: ' + $s.finish_thresholds_met); "
    "$p2 = 'C:\\Users\\tjarman\\repos\\foreman-scratch\\.foreman\\runs\\48b8aee8347a\\events.jsonl'; "
    "$ev = Select-String -Path $p2 -Pattern 'FOREMAN_ASSESSED' | ForEach-Object { ConvertFrom-Json $_.Line }; "
    "$ev | Select-Object -Last 3 | ForEach-Object { "
    "$a = $_.payload.assessment; "
    "Write-Output ('it{0} impl={1:N2} tests={2:N2} finish={3:N2}' -f "
    "$_.payload.iteration, $a.implementation_complete, $a.tests_sufficient, $a.ready_to_finish) } }"
)
r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", PS],
                   capture_output=True, text=True, timeout=150,
                   encoding="utf-8", errors="replace")
print(r.stdout[-2500:])
if r.stderr:
    print("STDERR:", (r.stderr or "")[-150:])
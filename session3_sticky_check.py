"""Analysis of run 7b74a56d:
- it6: finish raw = 0.37 >= 0.28 ✓, tests 0.47 >= 0.30 ✓, req 0.48 >= 0.30 ✓
  → ALL THREE crossed → sticky should have ARMED at it6!
- But sticky: False in the final state.

Why? The FINISH branch at it6: `not active_id` — at it6 a worker WAS active
(assessment during worker run). The flag `state.finish_thresholds_met = True`
should still be SET (the arm happens regardless of active). But the state
shows False...

The flag is set in decide() → state.finish_thresholds_met = True. Then the
runtime persists state... but the assessment that arrives at it6 might be a
DIFFERENT state object — the runtime may serialize state BEFORE policy runs
or use a copy. The flag mutation on the pydantic model may not persist if
the runtime re-assigns state from a serialized copy between iterations!

Actually the runtime serializes state.json after each assessment via
store; the mutation inside policy.decide SHOULD be captured when
state.model_dump() is called for persistence... unless runtime deep-copies.

Check runtime: does it re-read state.json or keep the in-memory object?"""
import subprocess

PS = (
    "Invoke-Command -ComputerName mraize -ScriptBlock { "
    "cd C:\\Users\\tjarman\\repos\\foreman; "
    "Select-String -Path src\\foreman\\policy.py -Pattern 'finish_thresholds_met' -Context 2,1 | "
    "ForEach-Object { $_.Context.PreContext; $_.Line; $_.Context.PostContext; '---' } }"
)
r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", PS],
                   capture_output=True, text=True, timeout=120,
                   encoding="utf-8", errors="replace")
print(r.stdout[-1200:])
if r.stderr:
    print("STDERR:", (r.stderr or "")[-150:])
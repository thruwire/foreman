"""smoothed_scores all ZERO — the EMA isn't updating. The gate:
`if self.config.use_smoothed_scores and self.config.score_smoothing_alpha < 1.0`
Both should be true (pilot sets use_smoothed_scores=True, alpha=0.4)...

Unless the pilot on mraize isn't using the new code — the pip -e . install!
The evidence commit earlier was pushed as raw files but the last EMA commit
modified policy.py + config.py — the editable install should pick it up...
BUT the evidence files were pushed as raw files + committed; the -e install
was only run in the previous session. Since -e . was installed, imports come
from the repo src. So the new code should be live.

Debug: check the config on mraize has the smoothing fields, and the policy
file has the EMA block."""
import subprocess

PS = (
    "Invoke-Command -ComputerName mraize -ScriptBlock { "
    "cd C:\\Users\\tjarman\\repos\\foreman; "
    "Select-String -Path src\\foreman\\policy.py -Pattern 'smoothed' | Select-Object -First 2 -ExpandProperty Line; "
    "Select-String -Path src\\foreman\\config.py -Pattern 'score_smoothing' | Select-Object -First 2 -ExpandProperty Line; "
    "& .venv\\Scripts\\python.exe -c \"import sys; sys.path.insert(0, 'src'); from foreman.config import FactoryConfig; c = FactoryConfig(); print('alpha default:', c.score_smoothing_alpha, 'use:', c.use_smoothed_scores)\" 2>&1 }"
)
r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", PS],
                   capture_output=True, text=True, timeout=200,
                   encoding="utf-8", errors="replace")
print(r.stdout[-1200:])
if r.stderr:
    print("STDERR:", (r.stderr or "")[-200:])
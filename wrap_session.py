"""Commit cord-side: grace test cleanup + calibrated pilot + push vault."""
import subprocess

subprocess.run(["git", "add", "-A"], cwd="C:/repos/foreman", capture_output=True, text=True)
r = subprocess.run(
    ["git", "commit", "-qm",
     "docs: calibration session findings (Jev scoring vs buffered hermes evidence)"],
    cwd="C:/repos/foreman", capture_output=True, text=True,
)
print("foreman commit:", "ok" if r.returncode == 0 else r.stdout[-200:] + r.stderr[-200:])
r2 = subprocess.run(["git", "log", "--oneline", "-3"], cwd="C:/repos/foreman", capture_output=True, text=True)
print(r2.stdout)
r3 = subprocess.run(
    ["git", "add", "-A"], cwd="C:/repos/obsidian-archive", capture_output=True, text=True)
r4 = subprocess.run(
    ["git", "commit", "-qm", "foreman mraize calibration session: fixes + data"],
    cwd="C:/repos/obsidian-archive", capture_output=True, text=True)
r5 = subprocess.run(["git", "push", "origin", "HEAD:main"], cwd="C:/repos/obsidian-archive",
                    capture_output=True, text=True)
print("vault push:", r5.stdout.strip().splitlines()[-1] if r5.stdout else r5.stderr[-200:])
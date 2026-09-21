"""Commit the grace work on cord's branch + push vault notes."""
import subprocess

r = subprocess.run(
    ["git", "add", "-A"], cwd="C:/repos/foreman", capture_output=True, text=True
)
r = subprocess.run(
    ["git", "commit", "-qm",
     "docs: mraize pilot findings - Jev calibration gap with buffered hermes output"],
    cwd="C:/repos/foreman", capture_output=True, text=True,
)
print("foreman:", r.stdout[-200:] or "committed")
r2 = subprocess.run(
    ["git", "log", "--oneline", "-3"], cwd="C:/repos/foreman",
    capture_output=True, text=True,
)
print(r2.stdout)
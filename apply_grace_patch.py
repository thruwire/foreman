"""Cold-start grace patch for foreman policy: a worker that has been alive
less than N seconds cannot be judged stuck/off-track — hermes boots + falls
back before producing evidence, and early assessments on empty diffs mislead
Jev. Implements a `cold_start_grace_seconds` config knob (default 0 = old
behavior)."""
import re

src = open("src/foreman/config.py").read()
if "cold_start_grace_seconds" not in src:
    src = src.replace(
        "    steering_grace_seconds: float = Field(default=30.0, ge=0.0)",
        "    steering_grace_seconds: float = Field(default=30.0, ge=0.0)\n"
        "    cold_start_grace_seconds: float = Field(default=0.0, ge=0.0)",
    )
    src = src.replace(
        '            "FOREMAN_STEERING_GRACE_SECONDS": ("steering_grace_seconds", float),',
        '            "FOREMAN_STEERING_GRACE_SECONDS": ("steering_grace_seconds", float),\n'
        '            "FOREMAN_COLD_START_GRACE_SECONDS": ("cold_start_grace_seconds", float),',
    )
    open("src/foreman/config.py", "w").write(src)
    print("config.py patched")
else:
    print("config.py already patched")

# policy: skip stuck/off-track/drift stop for workers younger than the grace window
pol = open("src/foreman/policy.py").read()
if "cold_start_grace_seconds" not in pol:
    anchor = "        if active_id:\n            off_track = assessment.work_off_track >= self.config.off_track_threshold"
    grace = (
        "        if active_id:\n"
        "            # Cold-start grace: a freshly launched worker (hermes boots in\n"
        "            # ~5-10s and streams no evidence until it works) cannot be\n"
        "            # meaningfully judged stuck/off-track. Skip the stop/steer branch\n"
        "            # until it has had the configured window to produce evidence.\n"
        "            _active = next((w for w in state.workers if w.worker_id == active_id), None)\n"
        "            _age = (_active.duration_seconds if _active and _active.started_at else None) or 0.0\n"
        "            if _age < self.config.cold_start_grace_seconds:\n"
        "                return result(\n"
        "                    InterventionType.CONTINUE,\n"
        "                    \"active worker is within the cold-start grace period\",\n"
        "                    active_id,\n"
        "                )\n"
        "            off_track = assessment.work_off_track >= self.config.off_track_threshold"
    )
    pol = pol.replace(anchor, grace)
    open("src/foreman/policy.py", "w").write(pol)
    print("policy.py patched")
else:
    print("policy.py already patched")
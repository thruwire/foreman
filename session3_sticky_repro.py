"""The deployed policy HAS the arm line (143). state.json written 15:48:17.
The it6 assessment (finish raw 0.37, all thresholds crossed) SHOULD have
armed the flag... unless the state object policy mutates is not the same
object later serialized, or the it6 assessment scores with SMOOTHED values
already applied differ from the raw ones I saw (the events.jsonl records
the RAW assessment BEFORE smoothing mutation? The events are emitted with
the assessment — in _assess the assessment is emitted BEFORE policy.decide
mutates it. The events show raw values. The policy then smooths (mutating
assessment fields) and checks thresholds against SMOOTHED values.

it6 raw: finish 0.37. Smoothed finish at it6 ≈ 0.31-0.33 (from the earlier
convergence data). Threshold 0.28 → smoothed finish CROSSES ✓. Smoothed
tests at it6 ≈ 0.38-0.45 ✓. Smoothed req ≈ 0.42 ✓. So finish_ready with
smoothed values should be True at it6 → sticky armed...

But the events.jsonl values I pulled were RAW (pre-mutation) because
events are recorded before decide() runs. The state.json's
finish_thresholds_met=False says the arm never happened or didn't persist.

Direct check: reproduce locally on cord with the mraize sequence and
alpha=0.4 to see if sticky arms."""
import subprocess

probe = r"""
import sys
sys.path.insert(0, r'C:/repos/foreman/src')
from foreman.config import FactoryConfig
from foreman.models import FactoryAssessment, FactoryState
from foreman.policy import FactoryPolicy

config = FactoryConfig(
    use_smoothed_scores=True, score_smoothing_alpha=0.4,
    finish_threshold=0.28, requirements_threshold=0.30, tests_threshold=0.30,
)
policy = FactoryPolicy(config=config)
state = FactoryState(run_id='r', job='j', repository='r', max_iterations=20)
sequence = [
    (0.06, 0.11, 0.06, 0.04), (0.49, 0.14, 0.25, 0.09), (0.53, 0.19, 0.28, 0.21),
    (0.60, 0.16, 0.35, 0.26), (0.61, 0.16, 0.42, 0.14), (0.55, 0.47, 0.48, 0.37),
    (0.46, 0.42, 0.43, 0.31), (0.48, 0.36, 0.46, 0.29), (0.45, 0.33, 0.41, 0.27),
]
for impl, tests, req, finish in sequence:
    a = FactoryAssessment(
        implementation_complete=impl, tests_sufficient=tests,
        requirements_satisfied=req, needs_verification=0.8, ready_to_finish=finish,
        meaningful_progress=0.6, worker_stuck=0.2, work_off_track=0.1,
        agents_md_drift=0.1, needs_human=0.2)
    iv = policy.decide(state, a)
    print(f"it finish_raw={finish:.2f} smoothed_finish={state.smoothed_scores.get('ready_to_finish', 0):.2f} "
          f"sticky={state.finish_thresholds_met} action={iv.action.value}")
"""
open("C:/repos/foreman/sticky_repro.py", "w").write(probe)
r = subprocess.run(
    [".venv/Scripts/python", "sticky_repro.py"],
    capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
)
print(r.stdout)
print(r.stderr[-200:] if r.stderr else "")
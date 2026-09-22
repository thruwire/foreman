# foreman — hermes worker backend fork

This is a maintained fork of [thruwire/foreman](https://github.com/thruwire/foreman)
(Jev-scored software factory). It exists because we run foreman on a fleet of
Windows boxes with the **Hermes Agent CLI** as the worker backend, and the
integration requires changes upstream has not merged.

**Canonical branch: `feat-hermes-worker-backend`** (set as default). Upstream
PRs #19/#20/#21 are open as a courtesy — if they're merged, we rebase;
if not, this fork is the permanent home.

## What's different from upstream

1. **Hermes Agent CLI worker backend** (`FOREMAN_WORKER_BACKEND=hermes`):
   non-interactive `hermes chat`, stream-json observation, steering via
   follow-up message. Same `Worker` protocol as the codex/opencode backends.
2. **Windows hardening**: `taskkill /T /F` terminate via `asyncio.to_thread`
   (hermes spawns child processes; plain terminate orphans them),
   `PYTHONUNBUFFERED=1` in the worker env.
3. **Capability probe**: `chat --help` is probed once; flags absent from older
   hermes builds are omitted (degrades to raw-text observation, never crashes).
4. **Cold-start grace** (`FOREMAN_COLD_START_GRACE_SECONDS`, default 0):
   young workers can't be judged stuck/off-track before producing evidence.
5. **Sticky finish thresholds**: once finish requirements are met at any
   assessment, a later noisy/idle assessment FINISHes instead of churning
   workers (`finish_thresholds_met` persisted in state.json).
6. **EMA score smoothing** (`FOREMAN_SCORE_SMOOTHING_ALPHA`, default 1.0 =
   off): threshold the smoothed estimate, not the raw per-assessment score.
   Jev scores oscillate independently; smoothing converges genuine progress.
7. **Evidence channel**: untracked-file contents in the observation, pytest
   summary parsing from worker output, and the runtime **test-verification
   hook** (`FOREMAN_VERIFY_TESTS_ON_COMPLETE`, default off) — foreman runs
   the repo's pytest itself after a coding worker completes and emits a real
   TEST_RESULT event. Backend-independent executed-test evidence.

Items 4-7 come from production calibration: before them, runs with buffered
worker output escalated at the worker cap; after, 3/3 consecutive
FACTORY_FINISHED (tests_sufficient 0.13 → 0.57 across a run).

## Fleet usage

- Deployed on cord (reference) and mraize (calibration + 3/3 FINISH).
- Mission preamble pins the repo path; workers are instructed not to commit
  (the git diff is the supervisor's evidence).
- `run_hermes_pilot.py` at the repo root is the calibrated pilot config.

## Sync policy

Rebase onto upstream `main` periodically (upstream is active). Own-side
changes live in clearly separable commits so conflict surface stays small.
Upstream's README documents the codex/opencode backends; this fork adds
`hermes` to that list.
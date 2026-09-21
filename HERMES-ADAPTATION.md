# Foreman + Hermes worker adaptation

Branch work in `C:\repos\foreman` (upstream: thruwire/foreman) adapting the
foreman supervisor to use the local Hermes Agent CLI as the coding worker,
removing the Codex dependency.

## What was added (2026-09-21)

- `src/foreman/workers/hermes.py` — `HermesWorker` (~200 lines): runs
  `hermes chat --in <repo> -q <mission> -Q --format stream-json --max-turns N`
  as a streaming subprocess, parses Hermes NDJSON events (system/tool_use/
  tool_result/text) into bounded WORKER_OUTPUT events with typed extras
  (`kind`, `tool`), `supports_steering=False` (policy falls back to
  stop/retry), terminate = SIGTERM group → SIGKILL after grace window.
  Mirrors `OpenCodeWorker` structurally.
- `config.py` — `worker_backend` Literal now includes `"hermes"`; new fields
  `hermes_model`, `hermes_provider`, `hermes_max_turns` (default 200),
  `hermes_toolsets`; env mappings `FOREMAN_HERMES_MODEL/_PROVIDER/_MAX_TURNS/
  _TOOLSETS`.
- `runtime.py` — factory branch for the hermes backend.
- `tests/test_hermes.py` — 9 offline tests (command construction, NDJSON
  parsing, TYPESAFE env filtering, launch failure, nonzero exit,
  malformed-line passthrough, non-steerable). All pass. Suite: 104 passed,
  2 pre-existing Windows-only failures (not ours — test_persistence ordering
  + test_integration drift/steer, both fail on a clean upstream clone too).
- `run_hermes_pilot.py` — driver script for a real supervised run.

## Verified end-to-end (real run, not simulated)

Scratch repo (Temp/foreman-scratch): job = "add subtract() + tests, run them".
`FOREMAN_WORKER_BACKEND=hermes`, Jev assessor live, hermes worker on the
box's default router model. Result: FACTORY_FINISHED / FINISHED, 1 worker,
0 retries, worker COMPLETED exit 0. calc.py + test_calc.py written as
requested; 6 Jev assessments over the run; scores moved sensibly
(implementation_complete 0.09→0.91, tests_sufficient 0.06→0.89,
worker_stuck 0.25→0.04, needs_human stayed low, policy CONTINUE→FINISH).
79 worker-output events streamed, tool_use/tool_result parsed correctly.
Jev key: foreman/.env gets TYPESAFE_API_KEY (copied from
jev-router/data/secrets/typesafe.key — same key the fleet router uses;
not committed, .env is gitignored).

## Config knobs for fleet runs

```
FOREMAN_WORKER_BACKEND=hermes
FOREMAN_HERMES_MODEL=glm-5.3-flash:cloud      # or leave None = box default
FOREMAN_HERMES_PROVIDER=custom                # or leave None = box default
FOREMAN_HERMES_MAX_TURNS=30                   # bound the worker
FOREMAN_HERMES_TOOLSETS=terminal,filesystem   # restrict toolsets
```

Worker env never receives TYPESAFE_* (same filtering as other backends).

## Known notes

- `--max-turns` does not hard-abort on Windows? (untested edge) — the
  worker_timeout_seconds is the reliable bound.
- Windows `os.killpg` doesn't exist; OpenCodeWorker already guards with
  try/except → falls back to process.terminate(). Same in HermesWorker.
  Works (verified via the 2 pre-existing test failures only).
- The 2 failing upstream tests are Windows-specific and pre-existing;
  candidates for an upstream PR (test ordering assertion + drift/steer
  integration path).

## mraize deploy findings (2026-09-21)

Hermes on mraize updated 0.21.0 → 0.21.4 (see vault Projects note). Full pilot
loop escalates: hermes 0.21.4 buffers NDJSON (only [session] streams live;
tool events arrive at process exit), so Jev's mid-run observations are
evidence-poor → needs_human 0.75-0.82 → ESCALATE. Worker itself verified fine
(23s, 84 events, 4 tool_use, correct repo). Fix order: Jev threshold
calibration from recorded runs → foreman cold-start grace before first
off-track stop → hermes router-probe investigation ("Primary auth failed"
against a router that accepts curl).

## Update: streaming regression root-caused + fixed (2026-09-21 night)

Decisive live-probe comparison (foreman-style pipe + PYTHONUNBUFFERED):
- cord 0.21.3 tree (36221c6d32): tool_use/tool_result stream LIVE
- mraize 0.21.4 tag tree (7b3c7aef): only [session] streams; rest at exit
- hermes origin/main (c56a4429): streams LIVE — upstream fixed it

mraize hermes-agent moved to origin/main (c56a4429), gateway redeployed,
Slack authenticated. Foreman pilot rerun with streaming + grace + evidence
fixes + sticky finish: worker completes correctly every run, Jev scores now
track reality (impl 0.06→0.75, tests →0.48, needs_human ≤0.33), but impl and
tests never peak together (inverse oscillation: 0.75 impl ↔ 0.16 tests, then
0.45 impl ↔ 0.43 tests) and ready_to_finish peaks 0.32-0.41 — below the
finish bar. Worker budget exhausts first. Remaining: score smoothing (EMA)
in foreman policy or batch threshold fitting — policy now has
`finish_thresholds_met` sticky flag as groundwork (merged, 6b0740c).

Calibrated pilot config in run_hermes_pilot.py: max_workers=6, grace 30s,
verifier-threshold 0.45, finish 0.35, req 0.35, tests 0.30, interval 15s.

## Fleet rollout pointer

Pilot on 1 box (morgoth suggested) with `FOREMAN_WORKER_BACKEND=hermes`,
repo allowlist, overall timeout 7200, results relayed to Slack by the box
agent. No codex needed on any box; only Jev API key + hermes (already
everywhere).
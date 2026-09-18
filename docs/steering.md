# Live steering

Foreman can send Jev-informed guidance into an active Codex turn before resorting to termination.
The feature uses Codex App Server because the non-interactive `codex exec` transport has no channel
for additional input during a turn.

## Decision flow

1. Foreman builds the same bounded factory observation used for every assessment.
2. Jev scores `worker_stuck`, `work_off_track`, `meaningful_progress`, and the other dimensions.
3. The deterministic policy checks safety limits and the worker's steering history.
4. The first stuck or off-track result at the configured threshold selects `STEER_WORKER`.
5. Foreman translates the scores into a bounded instruction and calls App Server `turn/steer` with
   the recorded thread and turn identifiers.
6. The worker receives a grace period. If the warning remains high afterward, policy stops it and
   uses the existing retry path.

Jev does not directly write the steering prompt or control the process. It supplies probabilities;
ordinary Python selects an allowed action and deterministically formats the guidance.

## State and observability

Each worker records its Codex thread ID, turn ID, steering count, last steering time, and steering
history. Successful and rejected attempts become `WORKER_STEERED` or `WORKER_STEER_FAILED` events.
The terminal prints accepted guidance live, and `foreman inspect` includes the event in the persisted
timeline.

## Configuration

The default backend is `app-server`, steering is enabled, one steering attempt is allowed per
worker, and the grace period is 30 seconds. Set `FOREMAN_CODEX_BACKEND=exec` to use the stable
non-interactive fallback; Foreman does not select steering while that backend is active. Steering
can also be disabled independently with `FOREMAN_STEERING_ENABLED=false`.

## Boundaries

- App Server is currently experimental and its protocol may evolve.
- A successful `turn/steer` response means Codex accepted the input, not that it followed it.
- Foreman suppresses repeated guidance with per-worker limits and a grace period.
- Human need and hard iteration limits still outrank steering.
- Stop, retry, timeouts, and escalation remain available when steering does not restore progress.

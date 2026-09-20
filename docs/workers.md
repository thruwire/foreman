# Worker backends

Foreman supervises *a coding agent*, not *Codex specifically*. The runtime
depends on the small `Worker` protocol in `src/foreman/workers/base.py` —
one capability and three coroutines:

- `supports_steering` — whether the worker has a live input channel.
- `run(record, repository, emit, timeout_seconds)` — start the agent with
  `record.mission` as its prompt, stream bounded stdout/stderr through
  `emit`, and record the terminal status on `record` before returning.
- `steer(message)` — deliver supervisory guidance into an in-flight agent.
  Return `False` if a delivery attempt is rejected. Backends without a live
  input channel declare `supports_steering = False`, so policy uses stop/retry
  without attempting delivery.
- `terminate(reason)` — stop the agent promptly: interrupt first, then
  kill after a bounded grace period.

## Built-in backends

| Backend | Selection | Steering |
| --- | --- | --- |
| Codex App Server | `FOREMAN_WORKER_BACKEND=codex` (default) + `FOREMAN_CODEX_BACKEND=app-server` (default) | Yes, into the active turn |
| Codex exec | `FOREMAN_CODEX_BACKEND=exec` | No — stop/retry only |
| OpenCode | `FOREMAN_WORKER_BACKEND=opencode` | No — stop/retry only |

The OpenCode backend shells out to `opencode run` in non-interactive mode.
The prompt is passed positionally and `--auto` keeps the headless run from
stalling on permission prompts. Auto mode approves requests that are not
explicitly denied and Foreman does not sandbox OpenCode, so configure restrictive
permission rules in `opencode.json` before running untrusted jobs. An optional
model can be pinned per worker when embedding Foreman
(`OpenCodeWorker(model="provider/model")`).

## Adding a backend

1. Implement the `Worker` protocol (see `OpenCodeWorker` for the
   subprocess template: bounded streaming, `start_new_session` process
   groups, interrupt-then-kill termination).
2. Keep the agent's environment clean: reuse `worker_environment()`, which
   inherits the process environment minus Foreman's `TYPESAFE_*` credentials.
3. Wire it into `FactoryRuntime`'s default worker factory (or pass your own
   `worker_factory`) and set `supports_steering` accurately. Unknown custom
   workers default to non-steerable for backward compatibility.
4. Cover it with offline tests: command construction, pipe streaming,
   launch failure, and `steer()` behavior. The suite must stay offline —
   no credentials, network, or real agent binaries.

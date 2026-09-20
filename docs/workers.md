# Worker backends

Foreman supervises *a coding agent*, not *Codex specifically*. The runtime
depends on the small `Worker` protocol in `src/foreman/workers/base.py` —
three coroutines:

- `run(record, repository, emit, timeout_seconds)` — start the agent with
  `record.mission` as its prompt, stream bounded stdout/stderr through
  `emit`, and record the terminal status on `record` before returning.
- `steer(message)` — deliver supervisory guidance into an in-flight agent.
  Return `False` when the backend has no live input channel; the policy
  then uses stop/retry for that backend instead of steering.
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
stalling on permission prompts; finer-grained permissions remain governed
by the user's `opencode.json`. An optional model can be pinned per worker
when embedding Foreman (`OpenCodeWorker(model="provider/model")`).

## Adding a backend

1. Implement the `Worker` protocol (see `OpenCodeWorker` for the
   subprocess template: bounded streaming, `start_new_session` process
   groups, interrupt-then-kill termination).
2. Keep the agent's environment clean: reuse `worker_environment()`, which
   inherits the process environment minus Foreman's `TYPESAFE_*` credentials.
3. Wire it into `FactoryRuntime`'s default worker factory (or pass your own
   `worker_factory`) and extend the policy's steering gate if the backend
   supports live input — the current gate is
   `worker_backend == "codex" and codex_backend == "app-server"`.
4. Cover it with offline tests: command construction, pipe streaming,
   launch failure, and `steer()` behavior. The suite must stay offline —
   no credentials, network, or real agent binaries.

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
| Hermes | `FOREMAN_WORKER_BACKEND=hermes` | No — stop/retry only |
| Deep Agents Code | `FOREMAN_WORKER_BACKEND=deepagents` | No — stop/retry only |

## Codex executable and model

By default, Foreman runs `codex` from `PATH` and leaves model selection to native Codex
configuration. Set `FOREMAN_CODEX_EXECUTABLE` to select another installed CLI and
`FOREMAN_CODEX_MODEL` to explicitly select the model for both coding and verifier workers.
The same settings are available as `codex_executable` and `codex_model` in `FactoryConfig`.
The App Server transport sends the model in `thread/start`; the exec transport passes `--model`.
`FOREMAN_DECISION_PROVIDER` and `FOREMAN_DECISION_MODEL` configure only the router and checks.

Model availability depends on the CLI version, authentication method, and account access. If Codex
rejects a model as unavailable or unsupported, Foreman escalates immediately with configuration
guidance. It does not choose another model or spend the retry budget repeating that request.
Check the installed CLI version and available models, then start a new run with a supported
configuration. Ordinary worker failures and transient transport errors keep their existing policy.
See the official [Codex model documentation](https://learn.chatgpt.com/docs/models) and
[CLI reference](https://learn.chatgpt.com/docs/cli/reference).

## Supervision ownership and output

Workers launched by `foreman run` receive `FOREMAN_MANAGED_WORKER=1`. A native `foreman hook`
invocation in that process tree returns an empty response before loading configuration or calling
a decision provider. The factory owns supervision for those workers, including the independent
verifier. This prevents a second supervisor from injecting competing completion guidance.
Standalone attached sessions retain their normal hook behavior. Custom worker implementations
should reuse `worker_environment()` so the same ownership marker applies.

The App Server transport preserves streamed output once. If Codex supplies only a completed item,
Foreman records its actual command output and exit code, agent text, or changed paths, rather than
an escaped copy of the full protocol object and patch. Test summaries remain readable by the
pytest parser. Verification lifecycle flags accompany the verifier results in decision evidence;
they do not replace the completion and test checks or change their thresholds.

## Pi and Pi Durable

Pi and Pi Durable use [attached supervision through their native bridges](../integrations/pi/README.md).
The host runs the agent, and the bridge forwards lifecycle events to Foreman for assessment
and feedback. These integrations do not currently implement the `Worker` protocol used by
`foreman run`, so they are not selectable with `FOREMAN_WORKER_BACKEND`.

## Backend behavior

The OpenCode backend shells out to `opencode run` in non-interactive mode.
The prompt is passed positionally and `--auto` keeps the headless run from
stalling on permission prompts. Auto mode approves requests that are not
explicitly denied and Foreman does not sandbox OpenCode, so configure restrictive
permission rules in `opencode.json` before running untrusted jobs. An optional
model can be pinned per worker when embedding Foreman
(`OpenCodeWorker(model="provider/model")`).

The Hermes backend shells out to `hermes chat` in headless single-query mode
(`-q <mission> -Q`), requesting newline-delimited JSON events with
`--format stream-json` when the installed Hermes build supports it (older
builds without the flag degrade to raw stdout text). The mission carries a
working-directory preamble so the agent operates in the repository even when
the CLI resolves its shell cwd elsewhere, and each NDJSON event is decoded
into compact `tool_use` / `tool_result` / session summaries on the event
stream. Command flags can be tuned per worker
(`HermesWorker(model=..., provider=..., max_turns=..., toolsets=...)`) or via
`FOREMAN_HERMES_EXECUTABLE`, `FOREMAN_HERMES_MODEL`, `FOREMAN_HERMES_PROVIDER`,
`FOREMAN_HERMES_MAX_TURNS`, and `FOREMAN_HERMES_TOOLSETS`. Like the other
non-interactive CLIs, Hermes has no live-turn input channel: `steer` always
returns `False` and the policy falls back to stop/retry.

The Deep Agents backend uses the separately installed `dcode` CLI
(`deepagents-code >= 0.1.83`, Python 3.12+). It runs `--non-interactive` with
`--max-turns`, `--timeout`, and a configurable `--shell-allow-list` (default
`recommended`). It streams bounded text chunks from both pipes and maps native
budget exit 124 to a timed-out worker. It does not opt into project hook trust
or configure a sandbox. See the [Deep Agents setup guide](../integrations/deepagents/README.md)
for model credentials, backend settings, and the separate native-hook path.

## Adding a backend

1. Implement the `Worker` protocol (see `OpenCodeWorker` for the
   subprocess template: bounded streaming, `start_new_session` process
   groups, interrupt-then-kill termination).
2. Keep the agent's environment clean: reuse `worker_environment()`, which
   inherits the process environment minus Foreman's `TYPESAFE_*` and `FOREMAN_OPENAI_*`
   credentials and marks factory-owned children to prevent duplicate hook supervision.
3. Wire it into `FactoryRuntime`'s default worker factory (or pass your own
   `worker_factory`) and set `supports_steering` accurately. Unknown custom
   workers default to non-steerable for backward compatibility.
4. Cover it with offline tests: command construction, pipe streaming,
   launch failure, and `steer()` behavior. The suite must stay offline —
   no credentials, network, or real agent binaries.

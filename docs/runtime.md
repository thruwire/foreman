# Runtime and event flow

## Components

```text
Codex App Server ─events─► FactoryRuntime ─snapshot─► ObservationBuilder
       ▲                         │                           │
       │                         │                           ▼
       │                  ResponsibilityRegistry ─checks─► JevForemanModel
       │                         │                           │
       └── steer / interrupt ◄── FactoryPolicy ◄─ForemanResult
                                 │
                                 ▼
                              RunStore
                         state.json + events.jsonl
```

- `CodexAppServerWorker` owns one App Server subprocess, thread, and active turn.
- `FactoryRuntime` owns lifecycle state, the event queue, and active worker tasks.
- `ObservationBuilder` gathers bounded worker, event, and Git evidence concurrently.
- `ResponsibilityRegistry` owns the active logical responsibilities and flattens their checks for
  one model call.
- `JevForemanModel` is the only module that imports the TypeSafe SDK.
- Each responsibility proposes zero or more directives from its check results.
- `FactoryPolicy` applies runtime guardrails and deterministically selects one directive.
- `RunStore` atomically replaces state and appends immutable events.

Simulation classes implement the same model and worker protocols. They exist for the demo and
offline tests; real runs default to Jev and Codex.

## Opt-in runtime test verification

When `verify_tests_on_complete` is set (`FOREMAN_VERIFY_TESTS_ON_COMPLETE=1`), the runtime runs
the repository's pytest suite itself after a coding worker completes, bounded by
`test_command_timeout` seconds (`FOREMAN_TEST_COMMAND_TIMEOUT`, default 120). This is
evidence-gathering, not a gate: the outcome is emitted as a `TEST_RESULT` event with parsed
`passed`/`failed`/`errored`/`skipped` counts and never raises. Disabled by default.

Verification is part of the coding worker's terminal lifecycle: it runs while the worker is still
tracked in `active_workers`, and the `TEST_RESULT` event is recorded before the terminal
`WORKER_COMPLETED` event is emitted (which carries a `verification` summary of the outcome).
The supervisor therefore cannot assess the completion event, select `FINISH`, and close the
factory while verification is still running — shutdown waits for in-flight verification rather
than cancelling it.

Process lifecycle: on timeout — or if the awaiting task is cancelled, e.g. by factory shutdown —
the subprocess — and, on POSIX, its whole process group, since the child starts in its own
session — is terminated (SIGTERM, then SIGKILL) and always waited on, so no zombies or orphaned
children survive. The structured `VerificationOutcome`
(`completed`/`timed_out`/`launch_failed`, return code, parsed summary) is what the event carries.

## One assessment cycle

1. A worker event enters the `asyncio.Queue`.
2. The watcher drains adjacent events and applies the minimum assessment interval.
3. Completion, failure, and stop events force an immediate cycle.
4. Git status/diff and current state become one bounded `FactoryObservation`.
5. The registry supplies all active responsibilities' checks to one Jev request.
6. Pydantic validates and groups the check outputs into one `ForemanResult`.
7. Responsibilities propose directives; policy records all proposals and selects one permitted
   directive in the same result.
8. Runtime may steer the active turn, interrupt it, or apply another lifecycle action.
9. The result, action, and any steering message are persisted before observation continues.

Foreman-generated events do not feed back into the queue, preventing the observer from triggering
itself recursively.

Before the first worker starts, real CLI runs load Foreman's central per-responsibility
configuration and route the incoming job. This configuration is independent of the target
repository. Global responsibilities are included directly; all conditional responsibilities are
evaluated together in one Jev call, and every threshold match is activated. The routing decision
is persisted as `FOREMAN_ROUTED`. See
[Responsibility configuration and routing](routing.md).

Interactive workers started outside Foreman use an assistant-specific protocol adapter around a
shared, single-event runtime. `foreman hook --client <adapter>` normalizes incoming events, keys
bounded global state by client and native session ID, routes each submitted prompt from the
built-in definitions and configured local extension snapshots, and applies the same observation,
Jev model, responsibility, and policy components.
It never creates repository-local run state. See
[coding-assistant hooks and attached workers](hooks.md).

Both managed and attached runtimes compose configured extensions from validated local snapshots
before routing. Activation is local-only. Managed runs persist `EXTENSIONS_ACTIVATED` before
`FOREMAN_ROUTED`; attached sessions pin extension IDs and snapshot revisions to prevent the active
responsibility set from changing silently during a session. See [Extensions](extensions.md).

## Shutdown

Worker timeout, overall timeout, escalation, and cancellation first request `turn/interrupt`.
If App Server does not complete the turn during the grace period, Foreman terminates the subprocess
group. Final state and the terminal event are persisted before the runtime closes the model client.

## Reading the code

Start with these modules:

1. `src/foreman/runtime.py` — concurrency and lifecycle.
2. `src/foreman/responsibilities/` — checks and directive proposals.
3. `src/foreman/policy.py` — directive arbitration and runtime guardrails.
4. `src/foreman/observation.py` — evidence boundaries.
5. `src/foreman/foreman/jev.py` — SDK isolation and batched check evaluation.
6. `src/foreman/workers/codex_app_server.py` — steerable App Server integration.
7. `src/foreman/workers/codex.py` — non-steerable `codex exec` fallback.

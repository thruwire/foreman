# Coding-assistant hooks and attached workers

`foreman run` starts and owns its coding workers. `foreman hook` covers the other direction: a
human starts an interactive coding-assistant session, and lifecycle events attach that existing
worker to Foreman.

The command is a single-event JSON filter:

```text
assistant-specific hook JSON
          │
          ▼
foreman hook --client <adapter>
          │
          ├── normalize to a Foreman hook event
          ├── packaged responsibility TOMLs
          ├── configured extensions from local snapshots
          ├── Jev routing and checks
          ├── local Git and AGENTS.md evidence
          └── global attached-session state
          │
          ▼
assistant-specific hook JSON
```

It does not run a service, register a worker over the network, or write Foreman state into the
target repository.

## Client adapters

The `--client` option selects the protocol adapter. Foreman does not guess from arbitrary input
JSON. It currently ships a `codex` adapter, which remains the default for compatibility:

```bash
foreman hook --client codex
```

An unknown client fails before Foreman creates or updates session state. Each adapter translates
its assistant's event names and fields into `HookEvent`, then renders Foreman's semantic
`HookOutcome` in the assistant's required response shape. The routing, observation, policy, and
session code only sees those normalized types. Adding another assistant therefore requires a new
adapter and registry entry, not another supervision runtime.

## Event flow

- `SessionStart` creates or refreshes the local session record.
- `UserPromptSubmit` treats the prompt as the current unit of work. Jev evaluates every
  conditional responsibility from the packaged TOMLs together, while global responsibilities are
  always included. Several responsibilities can match.
- `PreToolUse` records the proposed operation, builds current repository evidence, and evaluates
  every active responsibility's checks in one Jev request. A stop or escalation directive denies
  the tool call; steering becomes additional model context.
- `PostToolUse` records the tool arguments and result, then performs the same assessment. Steering
  becomes additional context, while stop or escalation output interrupts normal processing.
- `Stop` temporarily marks the attached worker complete so completion and verification
  responsibilities can decide whether the turn may finish. If work remains, Foreman requests one
  automatic continuation. `stop_hook_active` prevents an infinite continuation loop.
- `SessionEnd` deletes the attached-session record.

The Codex adapter validates input against the documented Codex event names and fields. Unsupported
or out-of-order events fail instead of manufacturing missing work context.

## Local state

Records live at:

```text
${FOREMAN_DATA_DIR:-~/.foreman}/sessions/<sha256-of-client-and-session-id>.json
```

The hash covers both the selected client and its native session identifier. This prevents path
injection and prevents two assistants with the same native session ID from sharing state. Files
are atomically replaced with owner-only permissions where the operating system supports them. A
per-session lock serializes concurrent hook processes. Stored event, result, intervention, error,
diff, and output history remains bounded by `FactoryConfig` limits.

Sessions expire after seven days of inactivity by default. Set
`FOREMAN_HOOK_SESSION_TTL_SECONDS` to change that lifetime, or pass `--data-dir` to isolate the
state directory during development and testing.

## Responsibility configuration

The hook runtime loads the TOML files packaged under
`src/foreman/responsibilities/definitions/` plus explicitly configured, installed extensions from
their validated local snapshots. It does not read target-repository responsibility files or
`FOREMAN_RESPONSIBILITIES_DIR`.

Hook processing never authenticates or synchronizes an extension. Those are explicit extension
lifecycle operations outside the latency-sensitive hook path. See [Extensions](extensions.md).
Coding-assistant plugin packaging remains separate; this command establishes the process protocol
that a future plugin can invoke.

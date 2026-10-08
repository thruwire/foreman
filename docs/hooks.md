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
JSON. It ships `codex`, `deepagents`, `pi`, and `pi-durable` adapters.
Codex remains the default for compatibility:

```bash
foreman hook --client codex
```

An unknown client fails before Foreman creates or updates session state. Each adapter translates
its assistant's event names and fields into `HookEvent`, then renders Foreman's semantic
`HookOutcome` in the assistant's required response shape. The routing, observation, policy, and
session code only sees those normalized types. Adding another assistant therefore requires a new
adapter and registry entry, not another supervision runtime.

Deep Agents Code uses native Hooks v2 command handlers. Run `foreman deepagents setup`
to merge the lifecycle handlers into its user configuration, then start a fresh `dcode`
session. `foreman hook --client deepagents` normalizes native session, prompt, tool,
failure, and stop events; it preserves `prompt_id` and `stop_hook_active`. Post-tool
stop directives become native feedback, while pre-tool and completion hooks provide
operation blocking and continuation control. See the
[Deep Agents setup guide](../integrations/deepagents/README.md) for credentials,
responsibilities, configuration preservation, native limitations, and verification.

Pi uses the TypeScript [bridge package](../integrations/pi/README.md), which wraps native events
in a session/cwd envelope and applies semantic response objects through Pi's native APIs. Its
events are `session_start`, `input`, `tool_call`, `tool_result`, `agent_before_settle`, and
`session_shutdown`. Input can consume a refused prompt before starting the agent; the final
actionable settle boundary supplies completion feedback and requests continuation.

Pi Durable uses native `GenerationTask` and `ToolTask` hooks: `beforeRequest`, `beforeTool`,
`afterTool`, and `onYield`. The bridge combines a stable storage identity with the conversation
ID, uses actual submitted entries as work, and deduplicates routing using their durable IDs.
Before-tool steering becomes a tool block because that hook cannot inject context. Generation
halt uses the public task abort API because ordinary hook exceptions are logged and swallowed.
See the bridge guide for setup, version support, and parallel-tool limitations.

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
- `Stop` temporarily marks the attached worker complete, runs command evidence providers selected
  by active checks, then lets the resulting Jev assessments and responsibilities decide whether the
  turn may finish. If work remains, Foreman requests one automatic continuation.
  `stop_hook_active` prevents an infinite continuation loop.
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

Before each Jev request, Foreman also applies an aggregate [request budget](evidence.md#jev-request-budgets).
An existing session with oversized retained history can therefore recover without deleting its
record. The current tool operation is supplied separately from shortened historical summaries,
so compaction cannot conceal the arguments being assessed.

Sessions expire after seven days of inactivity by default. Set
`FOREMAN_HOOK_SESSION_TTL_SECONDS` to change that lifetime, or pass `--data-dir` to isolate the
state directory during development and testing.

Clients may provide `work_id` to route a repeated original submission idempotently. The saved
work prompt, responsibility context, and one-continuation allowance survive retries and process
restarts. A new work ID resets the allowance. Optional `event_id` caches a completion response,
so replay between an external decision and a client's own commit does not spend that allowance
again. Reusing an ID with different input fails. The replay cache is bounded by event history
limits and expires with the session; it does not promise exactly-once model or tool execution.

## Responsibility configuration

### Central attached-worker configuration

Attached hooks accept controls in `${FOREMAN_CONFIG:-~/.foreman/config.toml}`:

```toml
[hooks]
repositories = ["/absolute/path/to/project", "/absolute/path/to/another-project"]

[hooks.responsibilities."core.completion"]
enabled = false

[hooks.responsibilities."core.verification"]
enabled = false

[hooks.responsibilities."example.project-context"]
kind = "declarative"
always = false
routing_instructions = "Does the incoming prompt require coding or debugging?"
routing_threshold = 0.75
context = "Consult the connected project before coding."
failure_action = "steer"
failure_message = "Read the connected project's current context before proceeding."

[hooks.responsibilities."example.project-context".checks.context_consulted]
instructions = "Was the connected project consulted for this work?"
min_threshold = 0.75
evidence = ["events", "history", "git.status"]
```

The repository list matches Git identities, including subdirectories, symlinks, and linked
worktrees. A different clone with the same name does not match. Omit `repositories` for the
existing global behavior; use `[]` to disable attached supervision everywhere. Excluded hooks
return an empty protocol response before loading extensions or initializing model clients, and
discard previous session state. The plugin's existing lifecycle hooks remain the entry point.

Every attached responsibility, including completion and verification, can be disabled with
`enabled = false`. Disable each unwanted class explicitly; unspecified responsibilities retain
their packaged defaults. This does not alter the required lifecycle checks for explicit
`foreman run` jobs. When all responsibilities are disabled, hooks initialize no model clients.

`kind = "declarative"` defines a local responsibility using Foreman's generic implementation.
It owns its routing instructions, context, and recurring checks without an extension package.
Each check asks whether a criterion is satisfied and requires `min_threshold`. A score below
that threshold proposes `failure_action`: `steer` (default), `stop`, or `escalate`.
The response uses `failure_message`, then `context`, or finally the failed check instructions.
Satisfied checks allow active work to continue and allow a completed turn to finish; higher
priority directives from other active responsibilities still take precedence.

Only matched responsibilities inject their `context` on prompt submission. When every candidate
is conditional and none matches, the previous work state is cleared and subsequent tool and
completion events add no checks. Startup validates definitions and rejects unknown kinds,
missing thresholds, and attempts to replace installed implementations with declarative ones.

Credentials may be supplied through the process environment or a protected `.env` in
`${FOREMAN_DATA_DIR:-~/.foreman}`. Existing environment values take precedence. These files and
the central responsibility configuration remain outside the repositories being supervised.
For OpenRouter, this same file can contain the SDK key and base URL described in the
[OpenRouter setup guide](openrouter.md#attached-hooks).

The hook runtime loads the TOML files packaged under
`src/foreman/responsibilities/definitions/` plus explicitly configured, installed extensions from
their validated local snapshots, then applies the central `[hooks.responsibilities]` overrides.
It does not read target-repository responsibility files or
`FOREMAN_RESPONSIBILITIES_DIR`.

Hook processing never authenticates or synchronizes an extension. Those are explicit extension
lifecycle operations outside the latency-sensitive hook path. See [Extensions](extensions.md).
Coding-assistant plugin packaging remains separate from the runtime. The
[Codex package](https://github.com/thruwire/marketplace/tree/main/plugins/foreman) invokes this
protocol and is published through the ThruWire marketplace. The placeholder under
`integrations/claude` reserves the future Claude Code adapter location but is not installable.

Command evidence is likewise assistant-neutral: a Codex, Pi, or Pi Durable bridge only forwards
lifecycle events to `foreman hook`. Foreman owns provider invocation and check-specific evidence,
so a CLI verifier does not need a separate SDK integration for every coding assistant. See
[Evidence providers](evidence.md).

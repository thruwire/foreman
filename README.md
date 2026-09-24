# Foreman

Foreman watches the software factory floor with [TypeSafe AI's Jev](https://docs.typesafe.ai/introduction),
placing a fast decision model above slower coding agents.

Give it a ticket, specification, bug report, or any free-form software job. A
[Codex](https://learn.chatgpt.com/docs/developer-commands?surface=cli) or
[OpenCode](https://opencode.ai) worker does the software engineering while Foreman independently
assesses whether the implementation is complete, requirements are satisfied, tests are sufficient,
verification is needed, or human input is required.

```text
                         SOFTWARE FACTORY
         Codex/OpenCode    Codex/OpenCode          Tests
            worker             worker                │
               │                  │                  │
               └──────────────────┼──────────────────┘
                                  │ factory evidence
                                  ▼
                              FOREMAN
                                Jev
                                  │
                                  ▼
                    responsibilities
                      completion
                        implementation_complete  .91
                        requirements_satisfied   .79
                        ready_to_finish          .21
                      verification
                        tests_sufficient         .34
                        needs_verification       .82
                      worker health
                        worker_stuck             .02
                        work_off_track           .06
                        meaningful_progress      .94
                      repository instructions
                        agents_md_drift          .01
                      human escalation
                        needs_human              .01
                                  │
                                  ▼
                    continue / steer / stop / retry
                         verify / finish
```

**Generative models work. Foreman watches the work.**

Foreman is an architectural experiment, not a claim that this design is already better than a
conventional coding-agent harness.

## Documentation

- [Theory: semantic supervision](docs/theory.md)
- [Why Jev fits the experiment](docs/why-jev.md)
- [What Foreman is proving](docs/what-foreman-proves.md)
- [Runtime and event flow](docs/runtime.md)
- [Responsibility configuration and routing](docs/routing.md)
- [Extensions](docs/extensions.md)
- [Coding-assistant hooks and attached workers](docs/hooks.md)
- [Live steering](docs/steering.md)
- [Worker backends](docs/workers.md)

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and pull-request
guidance. Please also read the [Code of Conduct](CODE_OF_CONDUCT.md) and report vulnerabilities
according to the [Security Policy](SECURITY.md).

## What is Foreman?

Foreman is a native Python `asyncio` runtime with two concurrent loops:

```text
CODING AGENT LOOP                         FOREMAN LOOP
reason                                    watch
  │                                         │
  ▼                                         ▼
tool                                      assess
  │                                         │
  ▼                                         ▼
observe ───────── factory events ────────► Jev
  │                                         │
  ▼                                         ▼
edit                                      decide (Python policy)
  │                                         │
  ▼                                         ▼
test ◄──────────── intervention ────────── intervene
  │
  └── continue
```

Foreman does not replace a worker's reason/tool/observe loop and does not choose its individual
tools or files. Missions stay broad. The important property is that the worker does not have to
stop working for the factory to think: worker output and lifecycle events flow into an independent,
debounced observation loop while the subprocess remains active.

## Why build this?

Coding agents are relatively slow, stateful generative systems. Supervisory questions such as
“is this worker stuck?” or “does this now need independent verification?” are narrower. Jev is
interesting here because TypeSafe describes it as accepting structured state and typed questions,
returning probabilistic decisions, and evaluating multiple questions independently in one parallel
request. Foreman explores whether that shape supports frequent semantic supervision without
rebuilding the coding agent itself.

## The factory floor

V1 runs one coding worker at a time. By default, a real worker is a Codex App Server thread and
turn launched over its JSONL protocol:

```text
codex app-server --listen stdio://
thread/start → turn/start → turn/steer or turn/interrupt
```

The App Server transport keeps the active Codex thread addressable, allowing Foreman to send a
supervisory update into an in-flight turn. App Server notifications and stderr are bounded in
memory, persisted as factory events, and made visible to Foreman before the worker exits. A verifier
is another worker using the selected backend with an independent, deterministic verification
mission. The prior stable `codex exec` transport remains available through
`FOREMAN_CODEX_BACKEND=exec`, but it cannot accept live steering.

The worker implementation is replaceable; the runtime depends on a small worker protocol rather
than Codex-specific types.

### Worker backends

The semantic-supervision loop is agent-agnostic. Select the worker backend with
`FOREMAN_WORKER_BACKEND` (`codex`, the default, or `opencode`):

```bash
FOREMAN_WORKER_BACKEND=opencode foreman run --repo ./my-project --job "Add request retries"
```

The OpenCode backend runs `opencode run` non-interactively and streams its output like the Codex
exec backend. Live steering into an active turn is only available with the Codex App Server
backend; other backends degrade to stop/retry. See [Worker backends](docs/workers.md) for the
`Worker` protocol and how to add your own.

## What Foreman watches

Each observation is compact and bounded. It contains:

- the original job and current factory status;
- active worker summaries, recent worker history, output tails, exit status, and elapsed time;
- `git status`, a bounded diff, and changed file names;
- bounded repository-root `AGENTS.override.md` or `AGENTS.md` instructions when present;
- verification results and recent persisted events;
- the prior Foreman result and intervention;
- attempt/failure counts and elapsed factory time.

Foreman never dumps the repository into Jev. Repository instructions are read for each observation
and included only in the transient Jev request; their contents are not persisted in factory state.
Defaults are a 20,000-character diff, 12,000 characters per captured output tail, 30 recent events,
and 10 workers of history. The limits live in `FactoryConfig` and can be changed for experiments.

## Responsibilities and checks

Foreman's semantic supervision is split into pluggable responsibilities. A responsibility owns one
or more Jev checks and deterministic logic that may propose directives. Checks have results;
responsibilities do not have aggregate scores. All active responsibilities can match at once, and
their checks are still sent to Jev in one parallel request.

The built-in completion responsibility owns:

- `implementation_complete`: probability that required implementation work is complete.
- `requirements_satisfied`: probability that the repository satisfies the free-form request as a
  whole, which is broader than code completion.
- `ready_to_finish`: probability that the factory should consider the job complete.

Verification owns `tests_sufficient` and `needs_verification`. Worker health owns:

- `meaningful_progress`: probability that the current or latest worker is advancing the job.
- `worker_stuck`: probability that the worker is looping, repeatedly failing, or unable to advance.
- `work_off_track`: probability that work is drifting from the original job or is unrelated.

Repository instructions owns:

- `agents_md_drift`: probability that the worker's behavior or repository work is materially
  inconsistent with the target repository's root `AGENTS.override.md` or `AGENTS.md` instructions.

Human escalation owns:

- `needs_human`: probability that judgment, credentials, clarification, or permission is needed.

Documentation quality is conditional. When incoming work explicitly requires repository
documentation, it owns `documentation_sufficient` and can request another worker pass when that
check remains below its TOML-defined minimum.

Every dimension is one Jev `Noul` question, whose result is the probability of “yes.” The ten
global checks—and the documentation check when routed—are sent in one request. A `ForemanResult`
groups check outputs by responsibility, records every
proposed directive, and identifies the one selected directive. It is stored in `state.json` and the
event timeline. The runtime accepts a `ResponsibilityRegistry`, so another responsibility can add
checks and directives without changing the Jev adapter or runtime loop.

For real runs, Foreman ships one TOML file per built-in responsibility under
`src/foreman/responsibilities/definitions/`. Each file owns its global-or-routed behavior, Jev
routing instructions, recurring Jev check IDs and instructions, thresholds, and
responsibility-specific settings. The Python class retains directive logic and any observation or
integration behavior, and declares the check IDs that behavior requires. An optional central
directory selected with `--responsibilities-dir` or `FOREMAN_RESPONSIBILITIES_DIR` can override
those files for the whole Foreman installation. Target repositories never supply responsibility
configuration. Foreman evaluates all non-global candidates in one Jev request and activates every
match; there is no separate routes file. See
[Responsibility configuration and routing](docs/routing.md).

## What Foreman can do

Jev only evaluates checks. Responsibilities propose directives, and a deterministic Python arbiter
selects the one action that is permitted:

- `CONTINUE`: let an active worker keep working.
- `START_WORKER`: begin a coding pass because work remains.
- `START_VERIFIER`: launch one independent verification pass.
- `STEER_WORKER`: send Jev-informed guidance into the active Codex turn.
- `STOP_WORKER`: gracefully terminate a stuck or off-track process.
- `RETRY_WORKER`: launch a fresh coding worker after a stopped attempt.
- `FINISH`: declare the job complete.
- `ESCALATE`: stop autonomous work and request human attention.

The ordering is safety-first: human need, iteration bounds, AGENTS.md drift, off-track/stuck
workers, retry handling, completion, verification, then continued work. A worker that crosses one
of those drift, stuck, or off-track thresholds is steered once by default. It receives a grace
period before a repeated high score causes Foreman to stop it. State tracks steering and
verification so policy does not oscillate. Transient supervisor (Jev) failures are tolerated up
to `FOREMAN_MAX_CONSECUTIVE_ASSESSMENT_FAILURES` consecutive misses — the workers keep running
while the assessment is retried — and only then does the run escalate.

Default policy thresholds are:

| Decision input | Threshold |
| --- | ---: |
| needs human | 0.80 |
| off track | 0.80 |
| AGENTS.md drift | 0.80 |
| worker stuck | 0.80 |
| needs verification | 0.65 |
| implementation before verification | 0.75 |
| ready to finish | 0.75 |
| requirements satisfied | 0.75 |
| tests sufficient | 0.75 |
| documentation sufficient, when routed | 0.75 |

These values are declared as `min_threshold` beside their checks in the responsibility TOMLs; the
table is only a consolidated view.

## Why Jev?

The integration follows TypeSafe's current official Python SDK:

- package: [`typesafe-sdk`](https://docs.typesafe.ai/sdk/python);
- async client: `AsyncTypeSafeClient`;
- authentication: `TYPESAFE_API_KEY`;
- model: `jev-latest`;
- call: `await client.system_one(state=..., questions=...)`;
- question types: `Noul`, `Choice`, and `Score`;
- timeout: configurable per client/call (the SDK default is 10 seconds);
- errors: typed API, authentication, rate-limit, connection, timeout, and response-validation
  exceptions;
- retries: the SDK supports status-aware backoff and `Retry-After`; Foreman retries 429 and
  transient 5xx failures within its assessment timeout.

[TypeSafe's primitives documentation](https://docs.typesafe.ai/primitives) says questions in a
single call are evaluated independently and in parallel. Noul is the right primitive for these ten
yes/no probabilities; Choice and Score remain available for future experiments. The public docs
describe HTTP 429 handling but do not publish a single numeric rate limit, so Foreman does not
invent one. Its minimum assessment interval defaults to five seconds and is configurable.

## Requirements

- Python 3.11 or newer.
- A TypeSafe API key for real runs. The deterministic demo and tests need neither service.
- For the default Codex backend: the
  [Codex CLI](https://learn.chatgpt.com/docs/developer-commands?surface=cli) on `PATH`, a version
  that provides `codex app-server` for live steering, and Codex authentication (`codex login`,
  then verify with `codex login status`).
- For the OpenCode backend: the [OpenCode CLI](https://opencode.ai) on `PATH` with an available
  provider and model.

## Installation

From a fresh checkout:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install '.[dev]'
cp .env.example .env
```

Put the key in `.env`:

```dotenv
TYPESAFE_API_KEY=your-key-here
```

`.env` is ignored by Git. Foreman never writes the key into logs, observations, state, or events.

## Running Foreman

```bash
foreman run \
  --repo ./my-project \
  --job "Add rate limiting to the API and make sure it is properly tested."
```

The terminal shows worker lifecycle messages and grouped job/factory-floor assessments. It makes
explicit when the coding agent is working and Foreman is independently watching, without animated
noise.

## Deterministic demo

The simulation exercises the same runtime, policy, persistence, event stream, and UI with
deterministic model and worker implementations:

```bash
foreman demo --repo .
```

It needs no API key, network, coding-agent CLI, or external repository. The sequence progresses from
continued implementation, through independent verification, to `FINISH`.

## Attached coding-assistant workers

`foreman hook` accepts one lifecycle event as JSON on stdin and writes the selected assistant's
hook JSON to stdout. It lets Foreman supervise an interactive session that a human started, rather
than only workers launched by `foreman run`:

```bash
printf '%s\n' '{"session_id":"thr_123","cwd":"/workspace/project","hook_event_name":"SessionStart","source":"startup"}' \
  | foreman hook --client codex
```

The explicit `--client` selects a protocol adapter; `codex` is currently the only adapter and
the compatibility default. Foreman does not infer a client from arbitrary JSON, and unknown
clients fail closed. The adapter normalizes events before they reach the shared attached-worker
runtime and translates semantic outcomes back into client-specific hook JSON.

Hook sessions are keyed by client plus its native `session_id` and stored globally under
`~/.foreman/sessions/`, not in the target repository. Hooks compose built-in definitions with any
configured extensions from validated local snapshots; they never authenticate or synchronize on
the hook path. A Codex plugin is not included yet. See
[coding-assistant hooks and attached workers](docs/hooks.md).

## Persistence and inspection

Each repository gets local, ignored state:

```text
.foreman/runs/<run-id>/
├── state.json
└── events.jsonl
```

`state.json` is atomically replaced and contains enough typed state to recover a run.
`events.jsonl` is an append-only timeline. Inspect either through the CLI:

```bash
foreman runs --repo ./my-project
foreman inspect <run-id> --repo ./my-project
```

The target repository's entire `.foreman/` directory is locally ignored because it contains run
state, not factory configuration.

## Runtime configuration

The most useful environment overrides are:

| Variable | Default | Meaning |
| --- | ---: | --- |
| `FOREMAN_ASSESSMENT_MIN_INTERVAL_SECONDS` | `5` | Debounce/coalescing floor |
| `FOREMAN_PERIODIC_ASSESSMENT_SECONDS` | `30` | Assessment during quiet work |
| `FOREMAN_JEV_TIMEOUT_SECONDS` | `10` | Semantic assessment timeout |
| `FOREMAN_WORKER_TIMEOUT_SECONDS` | `3600` | Per-worker timeout |
| `FOREMAN_OVERALL_TIMEOUT_SECONDS` | `7200` | Whole-run timeout |
| `FOREMAN_MAX_WORKERS` | `3` | Total workers, including verifier |
| `FOREMAN_MAX_RETRIES` | `1` | Fresh attempts after a stop |
| `FOREMAN_MAX_ITERATIONS` | `20` | Semantic decision ceiling |
| `FOREMAN_MAX_CONSECUTIVE_ASSESSMENT_FAILURES` | `3` | Tolerated supervisor failures before escalation |
| `FOREMAN_CODEX_BACKEND` | `app-server` | `app-server` for steering or `exec` fallback |
| `FOREMAN_WORKER_BACKEND` | `codex` | `codex` or `opencode` worker backend |
| `FOREMAN_STEERING_ENABLED` | `true` | Allow Jev-informed active-turn guidance |
| `FOREMAN_MAX_STEERS_PER_WORKER` | `1` | Steering attempts before stop/retry |
| `FOREMAN_STEERING_GRACE_SECONDS` | `30` | Time to recover before another intervention |
| `FOREMAN_RESPONSIBILITIES_DIR` | unset | Optional Foreman-wide responsibility overrides |
| `FOREMAN_DATA_DIR` | `~/.foreman` | Global configuration, extension, and session data |
| `FOREMAN_CONFIG` | `~/.foreman/config.toml` | Central extension configuration |
| `FOREMAN_HOOK_SESSION_TTL_SECONDS` | `604800` | Inactive attached-session lifetime |

Observation bounds and runtime limits remain typed `FactoryConfig` fields. Semantic minimums live
beside their checks in the central responsibility TOMLs. Other central settings can configure only
the responsibility that owns them.

## Tests

```bash
python -m pytest
```

The suite is offline: no credentials, network, Codex process, or external repository is required.
It covers models, serialization, persistence/recovery, Jev translation and failure handling, every
policy branch, subprocess streaming/termination, concurrent assessments, intervention delivery,
the complete simulated factory, and a stuck-worker recovery scenario.

## Process safety and security

Foreman enforces steering, worker, retry, iteration, worker-timeout, overall-timeout, and concurrency
limits. Workers receive only the supplied repository as their working root. Stop requests first
interrupt the active App Server turn, then terminate the process after a bounded grace period.
Ctrl-C cancels the run, terminates active workers, and persists a final cancelled state.

Workers still run with the permissions of the local environment. Codex requests its
`workspace-write` sandbox. OpenCode runs with `--auto`, which approves permission requests that are
not explicitly denied; Foreman does not add a sandbox around it. Configure restrictive OpenCode
permission rules before use. Foreman does not make untrusted repositories or jobs safe, so review
the worker configuration and repository before running either backend.

## Limitations

- Jev assessment accuracy is unproven for this use case and the semantic scores need calibration.
- False positives can stop useful workers; false negatives can allow bad work to continue.
- Repository observations are necessarily incomplete and bounded.
- The selected coding agent remains responsible for software-engineering reasoning and tool use.
- Codex App Server is currently experimental and its protocol may change between CLI releases.
- V1 runs one coding worker at a time.
- Local execution is not isolated.
- Persistence is useful for inspection, not production-grade durable execution.
- A verifier reports evidence through the same observation channel; there is no formal proof of
  correctness.
- This is an architectural experiment, not a production software factory.

## Future experiments

Natural next steps include simultaneous workers, per-worker and factory-wide assessments, alternate
coding agents or fast decision models, dynamic assessment frequency, calibrated policies, durable
execution, and isolated worker environments. They are intentionally outside this small V1.

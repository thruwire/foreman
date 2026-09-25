# Responsibility configuration and routing

Foreman routes each incoming job before starting the first worker. Routing is additive: every
global responsibility is active, and every conditional responsibility whose Jev score reaches its
threshold is added. Several responsibilities can match the same job.

Responsibility configuration belongs to the centralized Foreman installation, not to a managed
Git repository. There is no routes file and no target-repository discovery. Each responsibility
owns its routing semantics, recurring Jev checks, and settings in a file named for its
implementation. Its Python class owns directive logic and other executable behavior.

## Configuration locations

Foreman packages its built-in files here:

```text
src/foreman/responsibilities/definitions/
├── core.completion.toml
├── core.human-escalation.toml
├── core.verification.toml
├── core.worker-health.toml
├── quality.documentation.toml
└── repository.instructions.toml
```

Those files are the defaults for every run. An operator may select one optional, Foreman-wide
override directory with either:

```bash
foreman run --repo ./project --job "..." \
  --responsibilities-dir /etc/foreman/responsibilities
```

or `FOREMAN_RESPONSIBILITIES_DIR`. Files in that directory override packaged files with the same
name and may configure additional installed responsibility implementations. Omitted fields retain
their packaged values. The setting applies to every target repository supervised by that Foreman
process.

## File format

The installed documentation responsibility is conditional and provides a real routing example:

```toml
enabled = true
always = false
routing_instructions = """
Does the incoming work explicitly require creating or updating README content, user guides,
API documentation, release notes, or other documentation delivered with the repository?
"""
routing_threshold = 0.70

[checks.documentation_sufficient]
instructions = """
When documentation is required by the original job, is the relevant documentation complete,
accurate, and consistent with the implemented behavior?
"""
min_threshold = 0.75
```

- `enabled` includes or excludes the responsibility from routing.
- `always = true` makes it global. `always = false` requires `routing_instructions`.
- `routing_instructions` tells Jev when the responsibility applies.
- `routing_threshold` is that responsibility's activation threshold.
- `[checks.<id>]` defines each recurring Jev check and its instructions. The implementation class
  declares the check IDs its directive logic requires, and startup fails when any are missing.
- `min_threshold` sits beside the check it qualifies. Directive code reads it from the bound check
  instead of introducing responsibility-specific threshold setting names.
- `[settings]` is interpreted and validated by the responsibility implementation.

## Built-in responsibility settings

`core.worker-health` additionally accepts `cold_start_grace_seconds` (default `0.0`,
also `FOREMAN_COLD_START_GRACE_SECONDS`): while the active worker is younger than this
window, worker health defers judgment with `CONTINUE` instead of steering or stopping it,
because a freshly launched worker boots silently and streams no evidence until it starts
working. A worker with no recorded start time is not treated as young. The default `0.0`
disables the grace period.

Completion and verification are required global lifecycle responsibilities. Other built-ins may
be disabled or made conditional. Repository instruction paths are responsibility settings; they
identify files to inspect inside each target repository, but the settings themselves remain
central.

## Runtime flow

For a real `foreman run`:

1. Foreman loads packaged definitions and applies optional central overrides.
2. Global responsibilities become active immediately.
3. Jev evaluates every conditional candidate in one `system_one` request against the incoming job.
4. Foreman activates every candidate at or above its own threshold.
5. Candidate IDs, active IDs, routing scores, and `FOREMAN_ROUTED` are persisted with the run.
6. The selected TOML definitions provide Jev checks; their Python classes propose directives.
7. Only then does Foreman start the first worker.

Malformed configuration or routing output fails before a worker starts. Managed runs route their
initial job once. Attached workers handled through `foreman hook` route every normalized
`work_submitted` event, using built-in definitions and configured local extension snapshots; see
[coding-assistant hooks and attached workers](hooks.md).

Configured extensions can add namespaced responsibility implementations and hierarchical route
groups. A matching group may expose cached child candidates such as projects, then route into the
responsibilities beneath every matching child or the single `best_match` child. Group bindings and
the complete route trace are persisted with the routing decision. See [Extensions](extensions.md).

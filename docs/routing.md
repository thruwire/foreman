# Responsibility configuration and routing

Foreman routes each incoming job before starting the first worker. Routing is additive: every
global responsibility is active, and every conditional responsibility whose Jev score reaches its
threshold is added. Several responsibilities can match the same job.

Responsibility configuration belongs to the centralized Foreman installation, not to a managed
Git repository. There is no routes file and no target-repository discovery. Each responsibility
owns its routing semantics and settings in a file named for its implementation.

## Configuration locations

Foreman packages its built-in files here:

```text
src/foreman/responsibilities/definitions/
├── core.completion.toml
├── core.human-escalation.toml
├── core.verification.toml
├── core.worker-health.toml
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

The committed legal-review example is
[`examples/responsibilities/compliance.legal-review.toml`](../examples/responsibilities/compliance.legal-review.toml).
It demonstrates central configuration for a future installed implementation; copying the file
alone does not dynamically load Python code.

## File format

```toml
enabled = true
always = false
routing_instructions = """
Activate legal review when incoming work changes customer-facing terms, regulated workflows,
privacy disclosures, data-retention behavior, or other behavior requiring legal judgment.
"""
routing_threshold = 0.70

[settings]
review_project_id = "legal-review"
```

- `enabled` includes or excludes the responsibility from routing.
- `always = true` makes it global. `always = false` requires `routing_instructions`.
- `routing_instructions` tells Jev when the responsibility applies.
- `routing_threshold` is that responsibility's activation threshold.
- `[settings]` is interpreted and validated by the responsibility implementation.

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
6. The selected responsibilities provide checks and directives for the run.
7. Only then does Foreman start the first worker.

Malformed configuration or routing output fails before a worker starts. Routing subsequent user
messages is not part of this release.

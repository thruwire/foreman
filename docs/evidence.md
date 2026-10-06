# Evidence providers

Foreman's semantic checks consist of Jev instructions, a threshold, and an evidence selection.
Evidence providers collect bounded, typed fragments such as worker output, a Git diff, parsed test
results, or output from a trusted CLI. This keeps collection separate from judgment: providers
report what happened, while each check's instructions decide what that evidence means.

## Built-in providers and the default

Foreman currently models these built-in providers:

| Provider | Evidence |
| --- | --- |
| `worker` | Active workers, history, output tails, exit status, and timing |
| `git.status` | Git status and changed paths |
| `git.diff` | Tracked diff |
| `git.untracked` | Bounded, non-sensitive untracked-file excerpts |
| `repository.instructions` | Applicable root instruction file and contents |
| `tests` | Parsed test summaries from worker output |
| `verification` | Independent verifier-worker results |
| `events` | Recent bounded lifecycle events |
| `history` | Previous Jev result, intervention, and factory errors |

When a check omits `evidence`, it receives all built-in providers above. There are no named or
composable evidence sets. When a check supplies `evidence`, that list is its complete selection:

```toml
[checks.tests_sufficient]
instructions = """
Does the selected evidence show sufficient relevant test coverage and passing verification?
"""
min_threshold = 0.75
evidence = ["worker", "git.diff", "tests"]
```

Job identity, routing bindings, extension identity, iteration, attempts, and elapsed factory time
remain part of the check context. Built-in providers retain the original flat observation field
names in the state sent to Jev. Consequently, a check that omits `evidence` receives the same state
shape as before provider selection was introduced. An explicit list removes unselected fields;
selected command results appear in `command_evidence`.

Checks with the same evidence selection are evaluated together in one Jev call. Checks with
different selections are grouped into separate calls so each call receives only its selected
provider payloads.

## Jev request budgets

Every assessment is checked against two aggregate budgets after selecting evidence: state plus
the longest question, and state plus all questions. Routing requests use the same validation.
Per-field collection limits alone cannot prevent a long hook session from exceeding Jev's
[32k / 64k token limits](https://docs.typesafe.ai/models).

The SDK exposes no matching tokenizer. Foreman therefore counts ASCII-escaped JSON bytes,
including question definitions and envelope overhead, as a conservative proxy. Defaults are
30,000 bytes for the pair and 60,000 bytes for the request, leaving room below the published
token limits. These are byte budgets, not exact token counts. They can be lowered with
`FOREMAN_JEV_PAIR_BUDGET_BYTES` and `FOREMAN_JEV_REQUEST_BUDGET_BYTES`; values above the defaults
are rejected.

Foreman always removes exact duplicate worker output, even for small requests. Short repeated
strings remain in place when references would add more overhead. Whitespace differences remain
distinct. One canonical output stays in the request and other copies reference it.

Assessments aim for a soft target of 12,000 bytes for the pair and 24,000 bytes for the request.
Set `FOREMAN_JEV_PAIR_TARGET_BYTES` and `FOREMAN_JEV_REQUEST_TARGET_BYTES` to tune these targets.
Targets are clamped to the hard limits. If essential inputs alone exceed either target, the
request can use the hard limits and retain optional evidence within those limits. Routing
preserves its entire work and question definitions and only uses hard validation.

When selected evidence exceeds the effective target, Foreman drops oldest history first, then
shortens optional strings while retaining their beginning and end. Optional
fields can be removed as a final step. The request includes an `evidence_budget` record naming
affected fields and explaining that omitted evidence is unknown, not evidence of success or
absence. Compaction changes only the outgoing copy; saved session history remains intact.
Logs report serialized sizes without printing the evidence itself.
These steps are deterministic; Foreman does not generate summaries or invoke another model.

The full job, current hook operation and tool arguments, selected repository instructions,
routing bindings, and question definitions are preserved. If those essential inputs cannot fit,
Foreman reports a local budget error before submitting any assessment group. Existing hook
failure handling applies, including denial of `PreToolUse`. Routing preserves the entire work
and every routing question, and likewise fails locally if they cannot fit.

Successful API responses from both assessments and routing append metadata to
`${FOREMAN_DATA_DIR:-~/.foreman}/jev-usage.jsonl` (or the hook's explicit `--data-dir`). Each
record includes API-reported input/output tokens, model, purpose, and request bytes before and
after reduction. Missing counts are `null`, not zero; byte counts are never presented as tokens.
The log contains no task text, tool arguments, question instructions, evidence, or answers and
is created with owner-only permissions. Usage-write failures are logged and do not change
supervision. SDK retries without returned usage are not included in these counts. The append-only
log can be rotated or removed without changing session state.

To reduce routine cost, choose providers that support the actual check. For a project-stewardship
check that judges completed MCP reads, updates, and read-back evidence, a suitable starting point
is `evidence = ["events", "history", "git.status"]`. This retains lifecycle tool results, prior
outcomes/errors, and changed paths while excluding source diffs, file excerpts, and duplicate
worker transcripts. The complete job and current tool operation are still supplied. Completion
and code-quality checks can require broader evidence. Checks with different selections use
separate requests, so compare total reported input tokens across all requests when tuning.

## Command providers

Trusted command providers live in Foreman's central configuration, never in the repository being
supervised:

```text
${FOREMAN_CONFIG:-${FOREMAN_DATA_DIR:-~/.foreman}/config.toml}
```

Define a provider as an argv array:

```toml
[[evidence.commands]]
id = "pytest"
command = ["python", "-m", "pytest", "-q"]
timeout_seconds = 120
```

Then reference its namespaced ID from one or more checks:

```toml
[checks.tests_sufficient]
instructions = """
Considering the worker activity, diff, and pytest output, is verification sufficient?
"""
min_threshold = 0.75
evidence = ["worker", "git.diff", "command.pytest"]
```

An unreferenced command provider does not run. Unknown provider IDs fail configuration validation.
A command may be selected by several checks but is collected once per worker completion and shared
with those checks.

Commands execute sequentially with the target repository root as their working directory. Foreman
calls the executable directly; it does not invoke a shell, expand variables or globs, or interpret
pipes, redirects, semicolons, or command substitutions. If shell behavior is genuinely required,
point the argv array at an explicit, trusted wrapper executable.

For `foreman run`, referenced commands run after each successfully completed coding worker and
before `WORKER_COMPLETED`. For `foreman hook`, they run on the attached assistant's stopping event
before Foreman assesses whether the turn may finish. Failed workers do not trigger collection.

The provider payload contains:

- provider and triggering worker IDs;
- process status (`completed`, `timed_out`, or `launch_failed`);
- exit code;
- elapsed time;
- bounded stdout and stderr tails.

Foreman does not assign generic pass/fail semantics to the exit code and does not inject an
automatic directive. The paired check's Jev instructions interpret the complete provider payload;
its owning responsibility then uses the normal thresholds and policy to continue, verify, finish,
or escalate.

## Safety boundary

Command providers are trusted local code and run with Foreman's operating-system permissions. The
central-only command definition prevents a target repository from silently selecting an executable,
but a configured command can still execute repository-controlled scripts or configuration. Review
it as carefully as any CI command.

Foreman inherits the current environment, so avoid secrets in argv and prevent tools from printing
credentials. Output streams are continuously drained while only bounded tails are retained.
Timeouts terminate the subprocess group, including ordinary child processes, before Foreman
continues.

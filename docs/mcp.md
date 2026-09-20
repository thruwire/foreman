# Foreman as an MCP decision tool

`foreman mcp` starts a stdio MCP server (minimal JSON-RPC 2.0, no extra
dependencies) exposing one tool, `ask_foreman`. Add it to your coding
agent's MCP configuration and the agent can delegate multiple-choice
questions to the foreman instead of asking you:

```bash
foreman mcp --repo ./my-project
```

The server needs `TYPESAFE_API_KEY` like `foreman run` does (from the
repo's `.env` or the environment).

## The `ask_foreman` tool

```json
{
  "question": "Should I use a join or a subquery here?",
  "options": ["join", "subquery"],
  "context": "Refactoring the slow report query; both pass the tests.",
  "session_id": "optional, defaults to one id per server process",
  "risk_hint": "optional advisory hint about what the question involves",
  "extra_abstain_categories": ["external"],
  "min_confidence": 0.9
}
```

The foreman classifies the question, picks one of the options (or abstains
outright), and reports its confidence. The response is one of:

```json
{"status": "answered", "choice": "join", "rationale": "..."}
{"status": "abstained", "rationale": "..."}
```

When the foreman abstains, hand the question to the human. Abstaining never
terminates or escalates anything — unlike the supervision loop's `ESCALATE`,
which stops the run.

## Policy semantics

An answer is returned only when **all** of these hold:

1. the model did not abstain on its own,
2. its confidence is at or above the effective threshold,
3. the choice is one of the offered options,
4. the question's classification does not intersect the effective denylist.

The policy is layered and tighten-only:

- **Server-side baseline** (the safety floor): `FOREMAN_DECISION_THRESHOLD`
  (default `0.80`) and `FOREMAN_ALWAYS_ABSTAIN` (comma-separated list,
  default all four categories). The caller cannot weaken these.
- **Per-call** `min_confidence` and `extra_abstain_categories` can only
  tighten: the effective threshold is the maximum of the two, and the
  effective denylist is the union of both.

Question classification happens server-side in the decision prompt; the
caller's `risk_hint` is advisory. A dead decision model degrades to an
abstention, not a crash.

## Abstain categories

| Category       | Meaning                                                        |
|----------------|----------------------------------------------------------------|
| `destructive`  | Deletes or corrupts data or state                              |
| `irreversible` | Cannot be taken back: publishing, notifying, merging           |
| `external`     | Side effects beyond the local machine, incl. cost and exfiltration |
| `credentials`  | Authentication material, permission grants, signing            |

## Decision log

Every decision and abstention is appended as a `FOREMAN_DECIDED` event to
the repository's `.foreman/runs/<session-id>/events.jsonl`, recording the
question, classification, choice, confidence, and the effective policy — so
`foreman inspect <session-id>` shows the MCP-mode history and you can tune
the threshold and denylist from evidence.

## Client configuration

Add `foreman mcp` to your coding client's MCP configuration:

### OpenCode (`opencode.json` / `~/.config/opencode/opencode.json`)

```json
{
  "mcp": {
    "foreman": {
      "type": "local",
      "command": ["foreman", "mcp", "--repo", "."]
    }
  }
}
```

### Claude Desktop / Claude Code (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "foreman": {
      "command": "foreman",
      "args": ["mcp", "--repo", "."]
    }
  }
}
```

### Cursor (`.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "foreman": {
      "command": "foreman",
      "args": ["mcp", "--repo", "."]
    }
  }
}
```

Ensure `TYPESAFE_API_KEY` is exported in your shell environment or provided in your project `.env`.

## Instructing coding workers (`AGENTS.md`)

To direct coding agents to consult the foreman autonomously, include a scoped instruction in your repository or global agent prompt (e.g. `AGENTS.md`, `.cursorrules`, or custom system instructions):

```markdown
<!-- foreman-mcp:start -->
# Semantic Decision Supervision (foreman-mcp)

When the `ask_foreman` tool is available from the `foreman` MCP server, delegate operational and technical multiple-choice decisions to the Foreman before interrupting the human.

## When to use `ask_foreman`
- **Technical trade-offs:** Choosing between valid implementation approaches (e.g. join vs subquery, library selection, algorithm choice).
- **Error recovery & test fixes:** Deciding whether to retry, mock, refactor, or adjust test fixtures.
- **Autonomous workflows:** Any decision point during background loops, wave runs, or unattended tasks.

## When NOT to use `ask_foreman` (Ask the human directly)
- **User intent & product requirements:** Clarifying what feature the user wants or personal preferences.
- **Open-ended questions:** Questions without discrete, concrete options.
- **Credentials & authorization:** Handling secrets, API keys, or permission grants.
- **Irreversible / destructive actions:** Deployments, dropping databases, or deleting repositories.

## Protocol
1. Frame the decision as a multiple-choice question with discrete `options` and detailed `context`.
2. Call `ask_foreman(question=..., options=[...], context=...)`.
3. If Foreman returns `{"status": "answered", "choice": "..."}`:
   - Adopt the choice and cite Foreman's rationale in your explanation.
4. If Foreman returns `{"status": "abstained"}` or is unavailable:
   - Present the question, options, and context to the human user.
<!-- foreman-mcp:end -->
```

## Decision boundaries: Foreman vs. Human

Foreman is designed to decide bounded technical questions where clear options exist. It does not replace the human product owner.

| Category | Delegate to Foreman (`ask_foreman`) | Ask the Human Directly |
|---|---|---|
| **Product & Scope** | ❌ (Foreman cannot know user intent) | What features to include; UX preferences; domain logic |
| **Technical Trade-offs** | Join vs subquery; polling vs webhook; data structure choice | Major architectural rewrites; external vendor selection |
| **Test Failures & CI** | Flaky test retry vs mock; fixing fixture vs code; gate alignment | Deprecating entire test suites |
| **Merge & Branching** | Auto-resolving rebase conflicts; selecting merge strategy | Resolving semantic conflicts touching sensitive business logic |
| **Safety & Ops** | ❌ (Blocked by default abstain denylist) | Deployments; database drops; credential changes |

## Autonomous pipelines and multi-agent workflows

When orchestrating autonomous agents (e.g. wave orchestrators, PR review bots, overnight improvement loops), the "Foreman First" pattern prevents pipelines from stalling on operational questions:

1. **Unattended Execution:** Before raising an escalation or asking the user for input, the agent calls `ask_foreman`.
2. **Deterministic Fallback:** If Foreman answers, the agent proceeds immediately and logs the decision event. If Foreman abstains, the agent either logs an escalated blocker in its state file (in `--auto` mode) or pauses to prompt the operator.
3. **Sub-agent Delegation:** Sub-agents shepherding PRs or running tests can use `ask_foreman` to classify CI errors (transient runner queue vs reproducible code regression) and decide whether to retry or escalate without human intervention.

## Question formulation best practices

To maximize decision accuracy and avoid unnecessary abstentions:

- **Provide 2–4 mutually exclusive options:** Always ensure options are discrete and actionable (e.g. `["Retry with clean worktree", "Skip issue to continue wave", "Escalate to human"]`).
- **Provide rich context:** Pass relevant test output snippets, file paths, and the trade-offs of each option in the `context` parameter. The decider relies heavily on context to evaluate risk.
- **Set advisory `risk_hint`:** If the operation carries potential side effects, provide an advisory string (e.g. `risk_hint="CI retry"` or `risk_hint="merge conflict"`).
- **Graceful degradation:** Always check `status`. If `abstained`, treat it as an indication that the decision belongs with a human — abstaining is a safety feature, not an error.


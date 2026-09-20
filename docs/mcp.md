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

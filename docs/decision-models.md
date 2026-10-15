# Decision models and provider adapters

Foreman uses decision models for narrow semantic estimates about a coding agent's work. A decision
model receives evidence and named predicates and returns probabilities that those predicates are
true. Python responsibilities and policy turn those estimates into permitted interventions. Worker
backends remain independent of the decision provider.

## Architecture

```text
Observation + responsibility checks       Incoming work + routing predicates
                  │                                      │
        DecisionForemanModel                 DecisionResponsibilityRouter
        evidence grouping + budgets          thresholds + routing hierarchy
                  └──────────────────┬───────────────────┘
                                     │ named predicates
                           DecisionModelAdapter
                          /                    \
             JevDecisionAdapter       OpenAIDecisionAdapter
              system_one/Noul          /v1/decisions/predicate
                          \                    /
                              probabilities
                                     │
                         responsibilities + Python policy
```

`DecisionModelAdapter.evaluate(state, questions)` accepts a structured state mapping and an ordered
mapping of question IDs to instructions. It returns `DecisionResult`: probabilities by ID and a
transient raw response used only for metadata telemetry. An adapter declares `provider`, `model`,
and a default envelope `budget`, and implements `close()`. Provider SDK objects never enter
persistence or policy.

The shared assessment layer groups checks by their exact evidence selections, budgets every group
before submitting any requests, evaluates groups concurrently, and produces `ForemanResult`. The
shared router batches conditional predicates, includes global responsibilities without model calls,
and applies the configured thresholds and routing hierarchy. Both paths bound API waits and
translate failures into their existing public error types.

Only predicate semantics are supported in Foreman today. The result is the probability of the
predicate being true, not the confidence of a generated label. Provider Choice and Score features
are not exposed as responsibility checks. Probabilities still need calibration for each model and
workflow; changing providers does not establish equal accuracy or threshold behavior.

## Providers

| Provider | Adapter | Default model | API | Credentials |
| --- | --- | --- | --- | --- |
| `jev` | `JevDecisionAdapter` | `jev-latest` | TypeSafe `system_one`, `Noul` | `TYPESAFE_API_KEY` |
| `openai` | `OpenAIDecisionAdapter` | `gpt-6-luna` | OpenAI `POST /v1/decisions`, `predicate` | `FOREMAN_OPENAI_API_KEY`, falling back to `OPENAI_API_KEY` |

Jev uses the official TypeSafe SDK and retains `TYPESAFE_BASE_URL` support, including the
[OpenRouter route](openrouter.md). OpenAI uses the official async SDK's public `post` helper, so the
adapter works with SDK releases predating the generated Decisions resource. Install `pip install
'foreman-core[openai]'` (or `uv tool install 'foreman-core[openai]'`).

The [OpenAI Decisions API
reference](https://developers.openai.com/api/reference/resources/decisions/methods/create) documents
named predicate questions, ordered probability answers, and per-question refusals. Foreman sends its
JSON evidence as a text input and requires each answer's name and type to match its request.
Missing, extra, renamed, reordered, nonnumeric, nonfinite, out-of-range, or refused answers reject
that assessment or routing call. Existing runtime failure policy decides how to respond; no score is
invented for a refusal. Jev preserves its historical normalization, including clamping finite
numeric results into `[0, 1]`.

The [OpenAI data controls documentation](https://developers.openai.com/api/docs/guides/your-data)
currently lists `gpt-6-luna` for Decisions. `FOREMAN_DECISION_MODEL` lets an account select another
model when the endpoint supports it; Foreman does not probe availability or fall back silently. The
worker's generative model is configured separately.

## Configuration

Save provider settings in `${FOREMAN_DATA_DIR:-~/.foreman}/.env` for both managed runs and attached
hooks. Existing process variables take precedence. Managed runs retain their existing local `.env`
loading. Their new central `.env` loading is limited to decision settings and provider credentials;
it cannot select a worker backend or worker model. Central `OPENAI_API_KEY`, `OPENAI_BASE_URL`,
`OPENAI_ORG_ID`, and `OPENAI_PROJECT_ID` values are scoped to matching `FOREMAN_OPENAI_*` settings
rather than exported to workers. Hooks must receive the initial submitted work in a fresh session.

```dotenv
FOREMAN_DECISION_PROVIDER=openai
FOREMAN_DECISION_MODEL=gpt-6-luna
FOREMAN_OPENAI_API_KEY=your-key
FOREMAN_DECISION_TIMEOUT_SECONDS=10
```

Launched workers do not inherit `TYPESAFE_*` or `FOREMAN_OPENAI_*` variables. A worker's own
`OPENAI_API_KEY` and other native provider settings remain available. Use the dedicated Foreman key
to keep supervisor credentials separate from worker credentials. The adapter accepts
`FOREMAN_OPENAI_BASE_URL`, `FOREMAN_OPENAI_ORG_ID`, and `FOREMAN_OPENAI_PROJECT_ID`, falling back
to the SDK's normal process environment settings.

| Setting | Default |
| --- | ---: |
| `FOREMAN_DECISION_PROVIDER` | `jev` |
| `FOREMAN_DECISION_MODEL` | provider default |
| `FOREMAN_DECISION_TIMEOUT_SECONDS` | `10` |
| `FOREMAN_DECISION_PAIR_BUDGET_BYTES` | `30000` |
| `FOREMAN_DECISION_REQUEST_BUDGET_BYTES` | `60000` |
| `FOREMAN_DECISION_PAIR_TARGET_BYTES` | `12000` |
| `FOREMAN_DECISION_REQUEST_TARGET_BYTES` | `24000` |

The matching `FactoryConfig` fields use the `decision_` prefix. The historical `jev_` timeout and
budget constructor inputs, attributes, and `FOREMAN_JEV_*` environment variables remain compatible
aliases. Identical old/new settings are accepted; conflicting values are rejected.
`JevForemanModel`, `JevResponsibilityRouter`, `JevRequestBudget`, and `JevUsageRecorder` remain
importable compatibility entry points. Existing responsibility TOMLs need no changes.

Both providers append metadata to `decision-usage.jsonl` in the central data directory. Existing
`jev-usage.jsonl` files remain historical logs and are not rewritten. See [evidence budgets and
telemetry](evidence.md#decision-model-request-budgets).

## Adding an adapter

Implement `DecisionModelAdapter` in `src/foreman/decision_models/`. Keep transport, authentication,
retries, and provider response parsing there. Supply a provider-specific budget measurement when its
envelope differs, then compose `DecisionForemanModel` and `DecisionResponsibilityRouter` with the
adapter. Add configuration selection and offline contract tests for successful predicates, refusals,
malformed responses, deadlines, and client ownership. No worker or policy changes are needed to
support a provider with the same predicate semantics.

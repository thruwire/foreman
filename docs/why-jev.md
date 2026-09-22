# Why Jev fits the experiment

Foreman needs a model to turn heterogeneous factory evidence into several narrow, uncertain
judgments. TypeSafe AI's Jev has an interface shaped around that job.

## Interface fit

[TypeSafe's documentation](https://docs.typesafe.ai/introduction) describes Jev as a System One
model: structured state and typed questions go in, structured answers and probability distributions
come out. It exposes three primitives:

- [Noul](https://docs.typesafe.ai/primitives/noul): probability that a yes/no statement is true.
- [Choice](https://docs.typesafe.ai/primitives/choice): a selected category plus probabilities.
- [Score](https://docs.typesafe.ai/primitives/score): an expected position on an ordered rubric.

Foreman's built-in checks are all phrased so that a high value means “yes,” making Noul the direct
representation. For example, `worker_stuck = 0.88` is a probability, not a generated label that
must be parsed.

## Parallel questions

All active responsibilities' checks share the same compact observation and are submitted in one
`system_one` call. Their IDs and Jev instructions come from the active responsibilities' TOML
definitions. Ten checks are global; the conditional documentation responsibility contributes an
eleventh when incoming work explicitly requires documentation.
TypeSafe states that questions in a request are evaluated independently and in parallel. This is a
good fit for factory supervision: completeness should not have to be generated before stuckness,
and adding a safety dimension should not extend a token-by-token answer.

The initial responsibility router uses the same property in a separate call: all conditional
responsibilities are evaluated against the incoming job together, while global responsibilities
skip routing. Routing chooses which checks participate; it does not choose the eventual directive.

The model still can be wrong. “Typed” means the response shape is constrained; it does not prove
the semantic judgment. Foreman validates every field, handles timeouts and malformed responses,
persists the scores, and lets code decide whether an action is allowed.

## Speed as an architectural enabler

The experiment is interesting only if supervision is materially cheaper and faster than the work it
watches. TypeSafe positions Jev for low-latency structured decisions rather than text generation.
Foreman does not depend on a marketing latency number: calls have explicit timeouts, events are
debounced, and the assessment interval is configurable.

The practical hypothesis is relative: a narrow decision model may be called often enough to observe
a much slower coding worker during its run. The real integration test must measure that claim in the
user's environment.

## Exact integration

Foreman uses the official [`typesafe-sdk`](https://docs.typesafe.ai/sdk/python):

```python
async with AsyncTypeSafeClient(model="jev-latest") as client:
    result = await client.system_one(
        state=observation,
        questions={name: Noul(instructions=text) for name, text in questions.items()},
    )
```

Authentication comes only from `TYPESAFE_API_KEY`. The adapter translates SDK responses and errors
into Foreman's own models so persistence, policy, tests, and workers do not depend on SDK types.

Retries cover rate-limit and transient server errors and honor retry headers. TypeSafe documents a
429 error type but no universal numeric account limit, so the project makes no numeric rate-limit
claim.

## Why not ask Jev for an action?

The action depends on non-semantic invariants: whether a verifier already ran, how many workers are
allowed, whether retries remain, and whether finish thresholds are jointly satisfied. Those belong
in deterministic policy. Jev provides the uncertain state estimate; Python provides control.

# Decision-model adapter validation

Validation performed on 2026-10-13 for the decision-model adapter changes. This records actual
results from both live decision providers; it does not establish general model accuracy.

## Offline and package validation

- 466 Python tests passed against the source checkout and a wheel installed into a fresh Python
  3.12 environment. The clean environment used TypeSafe SDK 0.7.4 and OpenAI SDK 2.54.0.
- All 27 Pi/Pi Durable integration tests passed against the installed wheel.
- Ruff, local documentation links, and `git diff --check` passed.
- OpenAI contract tests use the real async SDK with an `httpx.MockTransport`. They exercise the
  Decisions URL and payload, evidence grouping, responsibility/group routing, refusals, malformed
  answers, HTTP errors, cancellation, client ownership, credentials, and both CLI supervision paths.
  These tests do not make live OpenAI API requests.

## Live Jev comparison

The existing `foreman-test-app` calculator was copied into isolated directories. Its original five
source/configuration files were hashed before testing and matched afterward; its pre-existing
working-tree changes were preserved. The test copies replace the repository's old smoke-test
instructions with instructions authorizing this isolated test.

The job adds `Calculator.square_root`, negative-input errors, parametrized tests, and README
examples, with independent verification. Actual pytest executions supply the evidence:

| Scenario | Test evidence | Implementation complete | Requirements satisfied | Ready to finish | Tests sufficient |
| --- | --- | ---: | ---: | ---: | ---: |
| Before implementation | 25 existing tests passed; requested feature absent | 0.06 | 0.04 | 0.13 | 0.27 |
| Implemented and independently verified | 33 tests passed | 0.95 | 0.91 | 0.91 | 0.94 |
| Intentional regression: return the input instead of its square root | 3 failed, 30 passed | 0.03 | 0.03 | 0.02 | 0.03 |

The adapter requested `jev-latest`; the API returned `jev-1.13.0`. Live routing activated the
conditional documentation responsibility with a score of 0.99. These are observations from one
small repository, not assertions of calibrated probabilities.

A separate full managed run used a real Codex App Server coding worker and independent verifier.
Both workers completed, and the verifier passed all 33 tests. The factory nevertheless ended in
`ESCALATED` at its two-worker limit because its final completion judgments did not meet policy
thresholds. Native Foreman hooks were also active inside the worker sessions. This run validates
live routing, assessments, worker integration, and verification transport; it does not validate a
successful end-to-end `FINISHED` outcome. No thresholds or policy were changed to force completion.
The coding worker used `gpt-5.5`, independently of Jev. Earlier worker connection attempts were
rejected by the local Codex CLI/account and produced no calculator changes.

## Live OpenAI comparison

The OpenAI adapter made real requests to `POST /v1/decisions` using the same frozen calculator
observations and the same six completion, verification, and documentation predicates as Jev.
The requested and returned model was `gpt-6-luna`. No worker was launched or worker model selected
for this comparison. A key from the user-authorized sibling repository was loaded only into the
test process; no credentials were copied into Foreman's repository or committed.

| Scenario | Implementation complete | Requirements satisfied | Ready to finish | Tests sufficient |
| --- | ---: | ---: | ---: | ---: |
| Before implementation | 0.00 | 0.00 | 0.00 | 0.02 |
| Implemented and independently verified | 0.99 | 0.95 | 0.97 | 0.97 |
| Intentional square-root regression | 0.01 | 0.00 | 0.00 | 0.00 |

Live OpenAI routing activated the conditional documentation responsibility with a score of 1.00.
The original calculator files still match the recorded hashes. Both providers scored the incomplete
and regressed states below the configured completion minimums and the verified state above them
in this controlled comparison. This validates the router/check adapters on this example, not a
successful full managed OpenAI worker lifecycle or calibrated model accuracy across repositories.

## Worker-model scope

`FOREMAN_DECISION_PROVIDER` and `FOREMAN_DECISION_MODEL` configure only routing and checks.
Worker-model fields and worker selection in the runtime are unchanged. The temporary worker
model overrides used in the separate managed experiment were local test-harness files outside
this repository and have been removed. Regression tests verify that central decision configuration
cannot change a managed worker's backend, model, existing OpenAI key, or existing OpenAI endpoint.

The PR remains unmerged for review, and no merge-gated release receipt is claimed.

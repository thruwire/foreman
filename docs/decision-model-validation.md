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

PR [#54](https://github.com/thruwire/foreman/pull/54) merged on 2026-10-15 as
`cfe4ad51aa85b2dd814322661b8931f94a4b904e`. Its merge receipt was stored in ThruWire Dev
and read back. The historical results above describe the adapter PR before the follow-up worker fixes.

## Managed-worker follow-up validation: 2026-10-15

The 0.5.1 candidate addresses two distinct problems: Codex worker model-access failures, and
incomplete or conflicting completion evidence. The worker model remains separate from the
Jev/OpenAI decision provider.

The original saved Jev managed run ended with `ready_to_finish=0.74`, below the unchanged 0.75
minimum, despite both workers exiting successfully. Its output also contained a native Foreman
`hookPrompt` asking for more implementation work inside the factory-owned verifier. Managed
workers now bypass that duplicate attached supervisor. Completed Codex App Server items expose
actual command output and agent text rather than escaped protocol objects and repeated patches.
Verification evidence includes explicit started/completed lifecycle flags.

A new Jev run exposed another evidence loss: compaction reduced a 2,758-character Git diff to
1,406 characters and omitted the new square-root implementation and negative-value documentation
in the middle. Its documentation score stayed below the minimum, and two coding workers exhausted
the test's worker budget without a verifier. Compaction now reduces bulky transcripts first.
Replaying that saved observation retains the complete small diff within the existing request
limits. Regression tests cover both Jev and OpenAI budget profiles. No decision thresholds changed.

A live connection probe reproduced the installed Codex 0.154.0 failure for the native
`gpt-6.1-sol` setting: Codex rejected it with a ChatGPT-account model error. Foreman classified
that response as a worker configuration error and retained the configured model. The complete
0.162.1 CLI succeeded with the same account and native model. This fixes neither account
entitlements nor an old CLI in place: deployments must select an installed compatible Codex
version using `PATH` or `FOREMAN_CODEX_EXECUTABLE`.

### Full managed runs

Both decision providers then supervised the same square-root job on fresh isolated copies of
`foreman-test-app`, starting with 25 passing tests. Both used the complete official Codex CLI
0.162.1 package and the same native `gpt-6.1-sol` worker configuration, without a model override.
No global Codex executable, authentication, or model configuration was changed. A command-execution
probe verified the temporary installation before the successful runs. An earlier probe with only
the CLI binary could connect but lacked `codex-code-mode-host`; those failed startup attempts
are excluded from the successful results below.

The harness allowed two workers, 40 assessments, a 300-second worker deadline, and a 480-second
factory deadline. Check definitions and minimum thresholds used the repository defaults. Test-copy
instructions directed workers to use the test Python environment and leave independent verification
to Foreman's separate verifier. User-authorized credentials were loaded only into the supervisor
process. The original test-repository files still matched their recorded hashes afterward.

| Decision provider / returned model | Run ID | Result | Coding tests | Independent verifier tests | Assessments | Duration |
| --- | --- | --- | --- | --- | ---: | ---: |
| Jev / `jev-1.13.0` | `9bcb6231232f` | `FINISHED` | 33 passed | 33 passed | 12 | 56.41 s |
| OpenAI / `gpt-6-luna` | `5fc31764bca7` | `FINISHED` | 33 passed | 33 passed | 12 | 55.80 s |

Each run launched exactly one coding worker and one verifier; both workers completed with exit
code zero. Neither worker transcript contained a duplicate native Foreman completion prompt.
Live documentation routing scored 0.99 for Jev and 1.00 for OpenAI. Final check scores were:

| Provider | Implementation complete | Requirements satisfied | Ready to finish | Tests sufficient | Documentation |
| --- | ---: | ---: | ---: | ---: | ---: |
| Jev | 0.95 | 0.92 | 0.92 | 0.95 | 0.92 |
| OpenAI | 1.00 | 0.98 | 0.99 | 0.98 | 0.97 |

### Intentional regression and offline checks

A further copy of the completed calculator replaced `return math.sqrt(value)` with `return value`.
Actual pytest execution returned exit code one with **3 failed, 30 passed**. That same failed
verification evidence was assessed through both live adapters using the built-in checks and
policy. This was an evidence replay, not another autonomous worker lifecycle. Jev scored
requirements/ready/tests at 0.02/0.02/0.02; OpenAI scored them at 0.00/0.00/0.00. Both selected
`START_WORKER`, and neither selected `FINISH`.

The candidate passes 483 Python tests from source and from its wheel installed into the clean
Python 3.12 environment, all 27 Pi/Pi Durable tests, Ruff, local documentation-link checks,
`git diff --check`, distribution builds, and `twine check`. Offline tests cover explicit worker
model/executable settings, provider model-error classification, escalation without a semantic API
call, duplicate-hook bypass, completed-item output, pytest parsing, and evidence compaction.
These live results validate this small repository and configuration; they do not establish general
model accuracy or guarantee completion on other jobs.

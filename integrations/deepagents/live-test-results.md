# Deep Agents live integration test

Tested on 2026-10-08 with the unpublished Foreman checkout, Python 3.12,
`deepagents-code==0.1.83`, `deepagents==0.7.23`, and
`openai:gpt-5.4-mini`. Foreman used real TypeSafe Jev requests for routing,
assessment, intervention, and completion.

## Sample and isolation

The source was `/Users/dev/Documents/GitHub/foreman-test-app`, including its
existing uncommitted changes. Its baseline had 25 passing calculator tests.
Two disposable Git copies were used for independent coding runs, each asked to
add `Calculator.modulo(a, b)`, Python remainder semantics, a specific zero-divisor
error, regression tests, and README usage.

The source repository's `AGENTS.md` prohibits tools and changes. Only the coding
copies received ordinary instructions to implement the feature and run tests.
A separate copy retained the original instructions for the hook intervention
test. SHA-256 checks confirmed all six original source files were unchanged.
The coding runs therefore validate the integration on the sample's code, rather
than claiming permission to change the original repository under its smoke-test
instructions.

An isolated Python environment, Deep Agents profiles, and Foreman data/config
directories kept these tests separate from the user's normal installations.
Credentials were loaded into the process environment and were not copied into
the sample repositories or test report. The shell allow-list was
`git,python,pytest,cat`.

## Foreman-owned worker

The installed `foreman run` command launched the actual `dcode` executable using
the new `deepagents` worker backend. Its profile had no Foreman hooks installed.

- The coding worker added the operation, tests, and documentation, then exited 0.
- Foreman launched a separate Deep Agents verifier, which inspected the changes
  and reran pytest, then exited 0.
- Foreman reached `FINISHED` and returned 0.
- An independent pytest run confirmed **36 tests passed**.

## Native hooks with an autonomous agent

The installed `dcode --non-interactive` command ran the same feature request in
the second copy. `foreman deepagents setup` installed the seven native handlers
into its isolated user profile. A recording wrapper passed each input unchanged
to the installed Foreman command and recorded its response and exit status.

| Event | Calls |
| --- | ---: |
| SessionStart | 1 |
| UserPromptSubmit | 1 |
| PreToolUse | 11 |
| PostToolUse | 11 |
| Stop | 1 |
| SessionEnd | 1 |

All 26 handlers exited 0 without stderr diagnostics. Foreman attached its
responsibilities at prompt submission, accepted completion at Stop, and removed
the session state at SessionEnd. The autonomous agent added the requested
feature, tests, and README examples, then exited 0. An independent pytest run
confirmed **34 tests passed**; the two agents produced different test matrices.

The successful coding run did not trigger a tool failure or Stop continuation.
Separate tests using the installed native Hooks v2 runtime and real Foreman/Jev
calls covered `PostToolUseFailure`, continuation followed by the one-continuation
limit, and repeated tool denial under the original prohibitive instructions.
Those tests explicitly dispatched native lifecycle events; they were not
autonomous model runs.

## Final checks

Both autonomous runs shut down their LangGraph servers; no server processes
remained. Foreman's automated suite passed **430 tests**, Ruff passed, and
`git diff --check` passed. These results cover the tested versions, model, and
local macOS environment; other providers and interactive terminal sessions
were not exercised in this test.

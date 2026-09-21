"""The runtime never emits TEST_RESULT events itself — test_results in the
observation comes solely from my parser on the latest worker's stdout.

The robust fix (now clearly the right one after 3 sessions of evidence):
foreman runs its OWN verification pytest after a worker completes, records
a real TEST_RESULT event. This gives Jev hard, backend-independent test
evidence. Implement as an ObservationBuilder enhancement: when building an
observation for an idle factory whose latest coding worker COMPLETED, run
`python -m pytest <changed test files> -q --no-header` (bounded 60s) and
parse the summary into test_results.

Design decisions:
- Only when: no active workers, latest coding worker COMPLETED (not
  failed/stopped — those don't warrant test runs).
- Only .py files in data: run full pytest -q with 60s timeout at the repo
  root; parse summary; cache result per worker_id so we don't re-run every
  15s assessment (cache in builder? No - store in state.test_results via
  runtime... but ObservationBuilder is stateless aside from store).
  Simplest: cache last-run (worker_id, results) on the builder instance.

Actually — mutating/running tests inside the OBSERVER blurs foreman's
design (observer shouldn't touch the repo). But pytest is read-only for
the source (it does execute code...). Given the pilot's goal, this is the
missing evidence channel; implement it in the runtime instead: after
worker completion, run pytest and emit TEST_RESULT event. That matches
the EventType that already exists.

Scope check: this is a bigger change. Time to do it properly in runtime.py:
after WORKER_COMPLETED (coding), if pyproject/pytest exists in repo, run
pytest -q (60s cap), parse, emit TEST_RESULT. That flows into
observation.test_results via recent_events + my parser on next assessment.
"""
import subprocess
print("implementing runtime test verification hook")
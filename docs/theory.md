# Theory: semantic supervision

## The separation

A coding agent and a supervisor solve different problems.

The coding agent has a wide action space. It reads code, forms plans, runs tools, edits files,
interprets failures, and tries again. This is slow, generative work. Preserving the agent's native
harness matters because its observations affect its next action.

The supervisor has a narrow action space. It asks questions about the process as a whole:

- Is useful work happening?
- Is the implementation probably complete?
- Is the evidence for testing strong enough?
- Is the worker stuck or drifting?
- Is an independent check now worthwhile?
- Does the situation require a person?

Those are semantic state-estimation problems. Their answers are uncertain, but the possible
interventions are few and can be constrained by ordinary code.

```text
wide generative loop                     narrow supervisory loop

reason → tool → observe → edit           evidence → probabilities
   ↑                       │                         │
   └──────── test ◄────────┘                         ▼
                                             deterministic policy
                                                    │
                                                    ▼
                                  continue / steer / stop / retry / verify
```

Foreman's central bet is that these loops should be separated and allowed to run concurrently.
The coding worker keeps its local reasoning loop. Factory events give the supervisor a bounded view
of what is happening without forcing the worker to yield.

## Semantic state, deterministic control

The model does not command processes. It estimates named probabilities. Python owns thresholds,
resource limits, lifecycle history, and the legal action vocabulary.

This split is important for three reasons:

1. Uncertainty stays visible rather than being hidden inside a generated instruction.
2. Safety invariants such as maximum retries and one verifier remain deterministic.
3. Policy can be calibrated or replaced without changing how evidence is assessed.

The architecture resembles a control system, but the state estimator is semantic rather than
physical. Evidence is partial, delayed, and lossy. The controller therefore needs hysteresis-like
state—verification history, worker counts, retry counts—and conservative terminal conditions.

## Why concurrency matters

If assessment only happens after a worker exits, the architecture is a serial judge:

```text
run agent → inspect result → run another agent
```

Foreman instead consumes start, output, failure, repository, timeout, and completion signals while
the subprocess is live. Events are coalesced so one noisy stdout stream does not produce one model
call per line. Important lifecycle events bypass the normal debounce delay.

That makes early intervention possible. A sufficiently strong AGENTS.md drift, stuck, or off-track
signal first sends guidance into the active turn. If the warning remains high after a grace period,
Foreman can still stop the worker before its natural timeout. Quiet work continues to receive
periodic assessment.

## What the theory does not assume

It does not assume the supervisor is always right, that probabilities are calibrated for software
work, or that more frequent judgment is automatically better. Those are empirical questions. The
project creates a small runtime in which they can be measured.

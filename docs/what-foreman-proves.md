# What Foreman is proving

“Proving” here means demonstrating architectural feasibility, not proving correctness of generated
software or superiority over another agent system.

## The claims this repository can demonstrate

1. **A coding worker and semantic supervisor can run concurrently.** The test suite records
   assessments whose observations contain an active worker.
2. **Noisy process evidence can become bounded model state.** Output, Git evidence, worker history,
   failures, prior decisions, and elapsed time are compacted before assessment.
3. **Probabilistic judgments can safely drive a small action vocabulary.** A deterministic policy
   gates model scores with thresholds, lifecycle history, and resource limits.
4. **A supervisor can intervene before worker completion.** Jev-informed guidance can enter an
   active Codex turn; repeated stuckness still reaches stop and retry paths.
5. **Independent verification can be a policy outcome rather than a hard-coded workflow step.** It
   starts only when the semantic evidence crosses the verification boundary and cannot loop.
6. **The experiment can be inspected.** State and an append-only event timeline reconstruct what the
   supervisor saw and did.

The deterministic simulation proves these software properties without depending on Jev or Codex.
A credentialed end-to-end run is required to demonstrate them with the external systems.

## The hypotheses it enables people to test

- Can Jev identify meaningful progress, stuckness, and off-track work early enough to help?
- Are its scores calibrated on real software jobs?
- Which observation fields carry useful signal?
- What assessment frequency balances responsiveness, cost, and noise?
- Do independent verifier passes improve completion quality?
- Which threshold ordering minimizes harmful interruptions and premature finishes?

The JSONL timeline makes these questions measurable. Runs can be labeled after the fact, threshold
changes can be replayed, and false positive/negative interventions can be counted.

## Claims this repository does not establish

- that Jev judgments are accurate for arbitrary repositories;
- that Foreman improves success rate, latency, cost, or safety;
- that `FINISH` means the implementation is correct;
- that local Codex execution is isolated or safe for untrusted code;
- that the default thresholds are calibrated;
- that the persistence layer has production durability.

A convincing evaluation would need a representative job set, blinded outcome labels, baselines,
cost and latency measurements, calibration curves for each dimension, and intervention ablations.
This V1 supplies the runtime and evidence trail for that later work.

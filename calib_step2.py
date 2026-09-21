"""Calibration fix 1: untracked-file evidence.

`git diff` never shows untracked files. hermes workers write test_calc.py as
a NEW file -> it's untracked -> the diff (Jev's main implementation evidence)
only shows calc.py, and the test file is invisible except as a name in
git_status. Fix: in ObservationBuilder, run `git add --intent-to-add`-free
approach — capture untracked file NAMES via status porcelain (already there)
and extend the diff evidence with `git diff --no-index /dev/null <file>`-style
content? Simpler + non-mutating: `git ls-files --others --exclude-standard`
list is already in git_status (?? lines). The real gap is CONTENT of untracked
test files.

Plan: add an `untracked_evidence` field to FactoryObservation: for each
untracked *.py/*.md file (bounded count+size), include the head of its
content. Then Jev can see test_calc.py's tests.

Steps:
1. observation.py: parse git_status ?? entries; read bounded content of each
   (up to 3 files, 4k chars each) into new field `untracked_evidence: str`.
2. Add to FactoryObservation model.
3. Unit test.
"""
import re

# 1. Model field
obs = open("src/foreman/observation.py").read()
if "untracked_evidence" not in obs:
    obs = obs.replace(
        "    git_status: str\n    git_diff: str",
        "    git_status: str\n    git_diff: str\n    untracked_evidence: str = \"\"",
    )
    open("src/foreman/observation.py", "w").write(obs)
    print("model field added")
else:
    print("model field already present")
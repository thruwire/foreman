"""FactoryState forbids extra - check the failing field. The extra_forbidden
error is likely from my WorkerRecord (worker_id duplicated earlier fixed) —
now the State: my tests pass max_workers=3 as kwarg but FactoryState may not
have it (it's on config). Check fields: run_id, job, repository, status,
iteration, max_iterations, workers... no max_workers. Remove those kwargs."""
import re

src = open("tests/test_policy_grace.py").read()
src = src.replace(
    "    state = FactoryState(\n        run_id=\"r1\", job=\"job\", repository=\"repo\", max_workers=3, max_iterations=20,\n    )",
    "    state = FactoryState(run_id=\"r1\", job=\"job\", repository=\"repo\", max_iterations=20)",
)
open("tests/test_policy_grace.py", "w").write(src)
r = __import__("subprocess").run(
    [".venv/Scripts/python", "-m", "pytest", "tests/test_policy_grace.py", "-q", "-p", "no:cacheprovider"],
    capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
)
print(r.stdout[-400:])
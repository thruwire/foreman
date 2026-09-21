"""Fix the duplicated worker_id kwarg in the second grace test."""
src = open("tests/test_policy_grace.py").read()
src = src.replace(
    '        worker_id=worker.worker_id if False else "worker-1",\n'
    '        status=WorkerStatus.RUNNING, started_at=started,',
    '        status=WorkerStatus.RUNNING, started_at=started,',
)
open("tests/test_policy_grace.py", "w").write(src)
print("fixed")
r = __import__("subprocess").run(
    [".venv/Scripts/python", "-m", "pytest", "tests/test_policy_grace.py", "-q", "-p", "no:cacheprovider"],
    capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
)
print(r.stdout[-400:])
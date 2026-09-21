"""Add a runtime test-verification hook: after a coding worker COMPLETES,
run the repo's test suite (bounded) and emit a real TEST_RESULT event with
parsed pytest data. This gives Jev hard test evidence regardless of the
worker backend's output behavior."""
import re

rt = open("src/foreman/runtime.py").read()

# 1. Find the worker-completed handling to hook after
m = re.search(r"(.*WORKER_COMPLETED.*\n)", rt)
print("WORKER_COMPLETED mentions:", len(re.findall(r"WORKER_COMPLETED", rt)))

# Find _worker_emit usage / where worker completion is emitted
idx = rt.find("async def _worker_emit")
print("worker_emit at:", idx)
snippet = rt[idx:idx+900] if idx > 0 else "NOT FOUND"
print(snippet[:900])
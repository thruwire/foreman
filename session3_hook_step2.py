"""Locate where WORKER_COMPLETED is emitted in _run_worker to insert the
test hook right after."""
import re

rt = open("src/foreman/runtime.py").read()
i = rt.find("async def _run_worker")
snippet = rt[i:i+3500]
print(snippet)
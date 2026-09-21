"""Debug the 2 parser failures: check what the regex matches."""
import sys
sys.path.insert(0, "src")
from foreman.observation import _pytest_summary_from_output

print("failed+passed:", _pytest_summary_from_output("=========== 1 failed, 3 passed in 1.20s ==========="))
print("junk line:", _pytest_summary_from_output("just some text\n2 passed something else entirely"))
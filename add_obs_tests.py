"""Unit tests for untracked_evidence + pytest parser."""
test = '''
from pathlib import Path

from foreman.observation import ObservationBuilder, _pytest_summary_from_output, _untracked_evidence


def test_untracked_evidence_captures_new_test_file(tmp_path: Path) -> None:
    (tmp_path / "test_new.py").write_text("def test_x():\\n    assert True\\n", encoding="utf-8")
    evidence = _untracked_evidence(tmp_path, "?? test_new.py", 12000)
    assert "untracked: test_new.py" in evidence
    assert "test_x" in evidence


def test_untracked_evidence_skips_binary_and_dirs(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\\x00\\x01\\x02\\x00binary")
    (tmp_path / "subdir").mkdir()
    evidence = _untracked_evidence(tmp_path, "?? blob.bin\\n?? subdir/", 12000)
    assert evidence == ""


def test_untracked_evidence_truncates_large_files(tmp_path: Path) -> None:
    big = tmp_path / "big.txt"
    big.write_text("x" * 9000, encoding="utf-8")
    evidence = _untracked_evidence(tmp_path, "?? big.txt", 6000)
    assert "truncated" in evidence
    assert len(evidence) < 9000


def test_pytest_parser_extracts_pass_and_fail() -> None:
    output = "running...\\n================= 2 passed in 0.01s =================\\ndone"
    results = _pytest_summary_from_output(output)
    assert len(results) == 1
    assert results[0]["passed"] == 2
    assert results[0]["failed"] == 0


def test_pytest_parser_extracts_failures() -> None:
    output = "=========== 1 failed, 3 passed in 1.20s ==========="
    results = _pytest_summary_from_output(output)
    assert results[0]["passed"] == 3
    assert results[0]["failed"] == 1


def test_pytest_parser_ignores_non_summary_lines() -> None:
    assert _pytest_summary_from_output("just some text\\n2 passed something else entirely") == []
'''
open("tests/test_observation_evidence.py", "w").write(test)
import subprocess
r = subprocess.run(
    [".venv/Scripts/python", "-m", "pytest", "tests/test_observation_grace_probe.py",
     "tests/test_observation.py", "-q", "-p", "no:cacheprovider"],
    capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
)
r2 = subprocess.run(
    [".venv/Scripts/python", "-m", "pytest", "tests/test_observation.py", "-q", "-p", "no:cacheprovider"],
    capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
)
print(r2.stdout[-400:])
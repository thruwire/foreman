from pathlib import Path

from foreman.observation import _pytest_summary_from_output, _untracked_evidence


def test_untracked_evidence_captures_new_test_file(tmp_path: Path) -> None:
    (tmp_path / "test_new.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    evidence = _untracked_evidence(tmp_path, "?? test_new.py", 12000)
    assert "untracked: test_new.py" in evidence
    assert "test_x" in evidence


def test_untracked_evidence_skips_binary_and_dirs(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02\x00binary")
    (tmp_path / "subdir").mkdir()
    evidence = _untracked_evidence(tmp_path, "?? blob.bin\n?? subdir/", 12000)
    assert evidence == ""


def test_untracked_evidence_truncates_large_files(tmp_path: Path) -> None:
    big = tmp_path / "big.txt"
    big.write_text("x" * 9000, encoding="utf-8")
    evidence = _untracked_evidence(tmp_path, "?? big.txt", 6000)
    assert "truncated" in evidence
    assert len(evidence) < 9000


def test_pytest_parser_extracts_pass_and_fail() -> None:
    output = "running...\n================= 2 passed in 0.01s =================\ndone"
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
    assert _pytest_summary_from_output("just some text\n2 passed something else entirely") == []


def test_untracked_evidence_covers_a_multi_file_chunk(tmp_path: Path) -> None:
    names = [f"pkg/m{i}.py" for i in range(5)]
    (tmp_path / "pkg").mkdir()
    for name in names:
        (tmp_path / name).write_text(f"# {name}\n", encoding="utf-8")
    status = "\n".join(f"?? {name}" for name in names)
    evidence = _untracked_evidence(tmp_path, status, 12000)
    for name in names:
        assert f"untracked: {name}" in evidence

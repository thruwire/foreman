from __future__ import annotations

import asyncio
import shutil

import pytest

from foreman.config import FactoryConfig
from foreman.models import FactoryState
from foreman.observation import ObservationBuilder, _untracked_evidence
from foreman.persistence import RunStore


def _status(*names: str) -> str:
    return "".join(f"?? {name}\n" for name in names)


def test_untracked_evidence_includes_text_file_content(tmp_path) -> None:
    (tmp_path / "new_test.py").write_text("def test_new():\n    assert True\n", encoding="utf-8")

    evidence = _untracked_evidence(
        tmp_path, _status("new_test.py"), file_limit=3, byte_limit=4096
    )

    assert "--- untracked: new_test.py ---" in evidence
    assert "def test_new():" in evidence


def test_untracked_evidence_respects_file_limit(tmp_path) -> None:
    for index in range(5):
        (tmp_path / f"file_{index}.py").write_text(f"# file {index}\n", encoding="utf-8")
    status = _status(*(f"file_{index}.py" for index in range(5)))

    evidence = _untracked_evidence(tmp_path, status, file_limit=2, byte_limit=100_000)

    assert "file_0.py" in evidence
    assert "file_1.py" in evidence
    assert "file_2.py" not in evidence


def test_untracked_evidence_respects_byte_limit(tmp_path) -> None:
    (tmp_path / "big.py").write_text("x" * 10_000, encoding="utf-8")
    (tmp_path / "small.py").write_text("y" * 10_000, encoding="utf-8")

    evidence = _untracked_evidence(
        tmp_path, _status("big.py", "small.py"), file_limit=3, byte_limit=100
    )

    # Only file-content bytes count toward the bound (header and truncation
    # note are metadata, not content).
    body = evidence.split("--- untracked: big.py ---\n", 1)[1].replace(
        "\n... (truncated, 10000 bytes total)", ""
    )
    assert len(body) <= 100
    assert "small.py" not in evidence


def test_untracked_evidence_truncates_large_files(tmp_path) -> None:
    (tmp_path / "big.py").write_text("z" * 5_000, encoding="utf-8")

    evidence = _untracked_evidence(
        tmp_path, _status("big.py"), file_limit=3, byte_limit=1_000
    )

    assert "truncated, 5000 bytes total" in evidence
    assert "z" * 5_000 not in evidence


def test_untracked_evidence_byte_limit_counts_bytes_not_chars(tmp_path) -> None:
    # あ is 3 bytes in UTF-8: 201 characters but 601 bytes on disk.
    (tmp_path / "cjk.py").write_text("あ" * 200 + "\n", encoding="utf-8")

    evidence = _untracked_evidence(tmp_path, _status("cjk.py"), file_limit=3, byte_limit=100)

    assert "truncated, 601 bytes total" in evidence
    body = evidence.split("--- untracked: cjk.py ---\n", 1)[1].replace(
        "\n... (truncated, 601 bytes total)", ""
    )
    assert len(body.encode("utf-8")) <= 100


def test_untracked_evidence_skips_binary_files(tmp_path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02binary\xff\xfe" * 100)
    (tmp_path / "ok.py").write_text("print('ok')\n", encoding="utf-8")

    evidence = _untracked_evidence(
        tmp_path, _status("blob.bin", "ok.py"), file_limit=3, byte_limit=4096
    )

    assert "blob.bin" not in evidence
    assert "ok.py" in evidence


def test_untracked_evidence_skips_sensitive_names(tmp_path) -> None:
    (tmp_path / ".env").write_text("API_KEY=hunter2\n", encoding="utf-8")
    (tmp_path / "id_rsa").write_text("PRIVATE KEY DATA\n", encoding="utf-8")
    (tmp_path / "deploy_token.txt").write_text("token-data\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("harmless notes\n", encoding="utf-8")

    evidence = _untracked_evidence(
        tmp_path,
        _status(".env", "id_rsa", "deploy_token.txt", "notes.txt"),
        file_limit=5,
        byte_limit=4096,
    )

    assert "hunter2" not in evidence
    assert "PRIVATE KEY DATA" not in evidence
    assert "token-data" not in evidence
    assert "harmless notes" in evidence


def test_untracked_evidence_expands_untracked_directories(tmp_path) -> None:
    subdir = tmp_path / "new_tests"
    subdir.mkdir()
    (subdir / "test_new.py").write_text("def test_new():\n    assert True\n", encoding="utf-8")

    evidence = _untracked_evidence(tmp_path, _status("new_tests/"), file_limit=3, byte_limit=4096)

    assert "--- untracked: new_tests/test_new.py ---" in evidence
    assert "def test_new():" in evidence


def test_untracked_evidence_ignores_missing_files(tmp_path) -> None:
    evidence = _untracked_evidence(tmp_path, _status("gone.py"), file_limit=3, byte_limit=4096)

    assert evidence == ""


def test_untracked_evidence_does_not_follow_symlinks(tmp_path) -> None:
    real = tmp_path / "real.txt"
    real.write_text("real content\n", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(real)

    evidence = _untracked_evidence(
        tmp_path, _status("link.txt"), file_limit=3, byte_limit=4096
    )

    assert evidence == ""


def test_untracked_evidence_empty_without_untracked_files(tmp_path) -> None:
    assert _untracked_evidence(tmp_path, "", file_limit=3, byte_limit=4096) == ""
    assert (
        _untracked_evidence(tmp_path, " M tracked.py\n", file_limit=3, byte_limit=4096)
        == ""
    )


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_observation_includes_untracked_evidence_from_git_status(tmp_path) -> None:
    proc = await asyncio.create_subprocess_exec("git", "init", "-q", cwd=tmp_path)
    assert await proc.wait() == 0
    (tmp_path / "new_feature.py").write_text("def feature():\n    return 1\n", encoding="utf-8")
    (tmp_path / "data.bin").write_bytes(b"\x00\xff" * 512)
    state = FactoryState(run_id="run-1", job="job", repository=str(tmp_path))
    store = RunStore(tmp_path)
    store.initialize(state)

    observation = await ObservationBuilder(store, FactoryConfig()).build(state)

    assert "def feature():" in observation.untracked_evidence
    assert "data.bin" not in observation.untracked_evidence


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_observation_includes_untracked_evidence_inside_new_directory(
    tmp_path,
) -> None:
    proc = await asyncio.create_subprocess_exec("git", "init", "-q", cwd=tmp_path)
    assert await proc.wait() == 0
    new_dir = tmp_path / "new_tests"
    new_dir.mkdir()
    (new_dir / "test_feature.py").write_text(
        "def test_feature():\n    assert True\n", encoding="utf-8"
    )
    state = FactoryState(run_id="run-1", job="job", repository=str(tmp_path))
    store = RunStore(tmp_path)
    store.initialize(state)

    observation = await ObservationBuilder(store, FactoryConfig()).build(state)

    assert "new_tests/test_feature.py" in observation.untracked_evidence
    assert "def test_feature():" in observation.untracked_evidence


@pytest.mark.asyncio
async def test_observation_untracked_evidence_empty_without_git_repo(tmp_path) -> None:
    state = FactoryState(run_id="run-1", job="job", repository=str(tmp_path))
    store = RunStore(tmp_path)
    store.initialize(state)

    observation = await ObservationBuilder(store, FactoryConfig()).build(state)

    assert observation.untracked_evidence == ""

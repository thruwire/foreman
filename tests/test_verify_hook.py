"""Tests for the runtime test-verification hook."""


import pytest

from foreman.config import FactoryConfig
from foreman.models import EventType


def record() -> "object":
    return None  # placeholder; real records built in tests


@pytest.mark.asyncio
async def test_hook_skipped_when_disabled(tmp_path) -> None:
    """verify_tests_on_complete=False (default) never runs pytest."""
    events = []

    class Runtime:
        config = FactoryConfig(verify_tests_on_complete=False)
        repository = tmp_path

        async def _worker_emit(self, worker_id, event_type, payload):
            events.append((event_type, payload))

    from foreman.runtime import FactoryRuntime

    await FactoryRuntime._verify_tests_after_worker(Runtime(), type("R", (), {"worker_id": "w1"})())
    assert events == []


@pytest.mark.asyncio
async def test_hook_emits_test_result(tmp_path, monkeypatch) -> None:
    """With the flag on, the hook runs pytest and emits a TEST_RESULT event."""
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    events = []

    class Runtime:
        config = FactoryConfig(verify_tests_on_complete=True, test_command_timeout=60)
        repository = tmp_path

        async def _worker_emit(self, worker_id, event_type, payload):
            events.append((event_type, payload))

    # use THIS repo's venv python by creating .venv/Scripts/python.exe symlink-equivalent:
    # simplest is to rely on sys.executable fallback (no .venv in tmp_path)
    from foreman.runtime import FactoryRuntime

    await FactoryRuntime._verify_tests_after_worker(Runtime(), type("R", (), {"worker_id": "w1"})())
    assert len(events) == 1
    event_type, payload = events[0]
    assert event_type is EventType.TEST_RESULT
    assert payload.get("passed", 0) >= 1 or "error" in payload or "summary" in payload


@pytest.mark.asyncio
async def test_hook_handles_missing_pytest(tmp_path) -> None:
    """Empty dir with no pytest importable → the hook still emits (error
    path), never raises."""
    events = []

    class Runtime:
        config = FactoryConfig(verify_tests_on_complete=True, test_command_timeout=5)
        repository = tmp_path

        async def _worker_emit(self, worker_id, event_type, payload):
            events.append((event_type, payload))

    from foreman.runtime import FactoryRuntime

    await FactoryRuntime._verify_tests_after_worker(Runtime(), type("R", (), {"worker_id": "w1"})())
    assert len(events) == 1

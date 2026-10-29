"""Isolate all tests from local research data before importing application code."""

import os
import tempfile

import pytest

_session_data = tempfile.TemporaryDirectory(prefix="paper-research-tests-")
os.environ["PAPER_RESEARCH_DATA_DIR"] = _session_data.name


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path, monkeypatch):
    import history
    from backend import preset_store, prompt_log, task_paths, template_store
    from backend.task_manager import task_manager

    monkeypatch.setattr(task_paths, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(preset_store, "PRESETS_DIR", tmp_path / "presets")
    monkeypatch.setattr(template_store, "TEMPLATES_DIR", tmp_path / "templates")
    monkeypatch.setattr(prompt_log, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(history, "HISTORY_DIR", tmp_path / "history")
    prompt_log._store.clear()
    task_manager.tasks.clear()
    task_manager.cancel_flags.clear()
    task_manager.ws_subscribers.clear()
    task_manager._subscriber_loops.clear()
    task_manager._running.clear()
    yield
    assert not task_manager._running, "A test leaked a background task"


def pytest_sessionfinish(session, exitstatus):
    _session_data.cleanup()

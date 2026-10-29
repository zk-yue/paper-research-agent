"""Confirmed queries are persisted before retrieval begins, not at task completion."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

from backend import deep_search_agent as deep
from backend import executor
from backend.task_manager import task_manager
from backend.task_paths import task_metadata_file


@pytest.mark.parametrize("search_fails", [False, True])
def test_plan_visible_and_persisted_before_search_even_on_failure(monkeypatch, search_fails):
    task = task_manager.create_task(
        keywords=["robot memory"],
        topic="robot memory",
        search_mode="deep",
        deep_rounds=1,
    )
    task_manager.update_task(task.id, results={"existing": "preserve"})
    plan = {"topic_summary": "Robot memory", "seed_queries": ['"robot" "memory"']}
    monkeypatch.setattr(executor, "get_llm", Mock())
    monkeypatch.setattr(deep, "understand_topic", AsyncMock(return_value=plan))
    monkeypatch.setattr(executor, "save_history", Mock())
    observed = []

    async def search(*args):
        assert task.steps[0].status == "done"
        assert task.results["topic_plan"] == plan
        assert task.results["existing"] == "preserve"
        persisted = json.loads(task_metadata_file(task.id).read_text())
        assert persisted["results"]["topic_plan"] == plan
        assert persisted["status"] == "running"
        observed.append(True)
        if search_fails:
            raise RuntimeError("search unavailable")
        return []

    monkeypatch.setattr(deep, "_run_search", search)
    asyncio.run(executor.execute_task(task.id))
    assert observed == [True]
    assert task.status == ("failed" if search_fails else "completed"), task.error
    assert task.results["topic_plan"] == plan
    assert task.steps[0].status == "done"

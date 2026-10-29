"""Search evidence is available before summary generation and survives persistence."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock

from backend import executor
from backend.task_manager import task_manager
from backend.task_paths import task_metadata_file


def test_progress_preserves_all_papers_and_screening_audit(monkeypatch):
    task = task_manager.create_task(
        keywords=["robot memory"], topic="robot memory", search_mode="deep", quick_mode=True
    )
    papers = [
        {
            "title": f"Robot {i}",
            "abstract": "Memory for robot control",
            "relevance_score": 4,
            "matched_queries": ["robot memory"],
        }
        for i in range(3)
    ]
    screening = {
        "included": papers,
        "pending": [{"title": "Needs abstract"}],
        "excluded": [{"title": "Off topic"}],
    }
    snapshot = {
        "papers": papers,
        "screening": screening,
        "iteration_log": [{"round": 1, "found": 5, "new_retained": 3}],
        "coverage_status": "partial_sources",
    }

    async def run(**kwargs):
        kwargs["on_plan"]({"seed_queries": ["robot memory"]})
        kwargs["on_progress"](snapshot)
        persisted = json.loads(task_metadata_file(task.id).read_text())
        assert len(persisted["papers"]) == 3
        assert persisted["papers"][0]["matched_queries"] == ["robot memory"]
        assert persisted["results"]["screening"] == screening
        assert persisted["status"] == "running"
        return {**snapshot, "summary": "", "representative": papers[:1]}

    monkeypatch.setattr(executor, "run_deep_search", run)
    monkeypatch.setattr(executor, "get_llm", Mock())
    translate = AsyncMock(return_value=papers)
    monkeypatch.setattr(executor, "_parallel_translate", translate)
    monkeypatch.setattr(executor, "save_history", Mock())
    asyncio.run(executor.execute_task(task.id))
    assert task.status == "completed", task.error
    assert len(task.papers) == 3
    assert task.results["screening"] == screening
    assert task.results["coverage_status"] == "partial_sources"
    translate.assert_not_awaited()

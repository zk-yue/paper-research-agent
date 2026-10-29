"""Completed real evaluations become reviewable tasks without another model call."""

import json

from backend.task_paths import task_metadata_file
from scripts.import_search_evaluation import import_result


def test_import_preserves_evidence_and_does_not_overwrite_evaluation(tmp_path):
    paper = {
        "title": "Robot planning",
        "abstract": "Visual planning study.",
        "published_date": "2025-01-01",
        "relevance_score": 4,
        "relevance_evidence": "Visual planning study.",
        "citation_from": [{"title": "Seed paper"}],
    }
    source = {
        "papers": [paper],
        "summary": "Supported review",
        "topic_plan": {"seed_queries": ["robot planning"]},
        "iteration_log": [{"round": 1, "queries": ["robot planning"]}],
        "screening": {
            "included": [paper],
            "supporting": [],
            "excluded": [],
            "pending": [],
            "counts": {"included": 1, "supporting": 0, "excluded": 0, "pending": 0},
        },
        "coverage_status": "bounded_results",
        "evaluation": {
            "topic": "robot planning",
            "year_from": 2021,
            "quick_mode": False,
            "started_at": "2026-10-29T00:00:00+00:00",
            "completed_at": "2026-10-29T00:10:00+00:00",
        },
    }
    path = tmp_path / "result.json"
    path.write_text(json.dumps(source))
    original = path.read_bytes()
    task_id = import_result(tmp_path)
    task = json.loads(task_metadata_file(task_id).read_text())
    assert task["status"] == "completed"
    assert task["results"]["screening"] == source["screening"]
    assert task["results"]["deep_research_summary"] == source["summary"]
    assert task["papers"][0]["citation_from"] == [{"title": "Seed paper"}]
    assert path.read_bytes() == original
    assert import_result(tmp_path) != task_id
    assert json.loads(task_metadata_file(task_id).read_text()) == task

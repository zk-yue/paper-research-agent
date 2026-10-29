import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend import reference_card, review_writer
from backend.schemas import PaperInfo
from backend.task_manager import task_manager


@pytest.fixture
def review_fixture(monkeypatch):
    task = task_manager.create_task(
        keywords=["tactile"], papers=[PaperInfo(title="Paper", url="https://example.test/paper")]
    )
    paper = task.papers[0].model_dump()
    key = reference_card.paper_hash(paper)
    card = {"paper_id": key, "title": "Paper", "confidence": "high"}
    monkeypatch.setattr(reference_card, "_find_pdf_for_paper", lambda *args: None)
    monkeypatch.setattr(review_writer, "get_llm", lambda: Mock())
    monkeypatch.setattr(
        review_writer,
        "plan_related_work",
        lambda *args: {
            "inferred_title": "Tactile policies",
            "included_references": [key],
            "warnings": [],
        },
    )
    return task, key, card


def test_review_pipeline_does_not_block_event_loop(review_fixture, monkeypatch):
    from backend import download_agent

    task, key, card = review_fixture
    main_thread = threading.get_ident()
    phases = []

    def download(*args):
        assert threading.get_ident() != main_thread
        phases.append("download")

    def extract(*args, **kwargs):
        assert threading.get_ident() != main_thread
        phases.append("extract")
        return {key: card}

    def generate(*args):
        assert threading.get_ident() != main_thread
        phases.append("generate")
        time.sleep(0.1)
        args[-1]["content"] = "Related work [1]."
        return "Related work [1]."

    monkeypatch.setattr(download_agent, "download_papers", download)
    monkeypatch.setattr(review_writer, "extract_reference_cards", extract)
    monkeypatch.setattr(review_writer, "generate_related_work_from_cards", generate)

    async def scenario():
        job = asyncio.create_task(review_writer.write_review(task.id, [0]))
        await asyncio.sleep(0.02)
        assert not job.done(), "The event loop was blocked until generation completed"
        return await job

    review_id = asyncio.run(scenario())
    review = task.results["custom_reviews"][0]
    assert phases == ["download", "extract", "generate"]
    assert review["id"] == review_id
    assert review["status"] == "done"
    assert review["content"] == "Related work [1]."


def test_failed_generation_with_partial_content_is_not_marked_done(review_fixture, monkeypatch):
    from backend import download_agent

    task, key, card = review_fixture
    monkeypatch.setattr(download_agent, "download_papers", lambda *args: None)
    monkeypatch.setattr(
        review_writer, "extract_reference_cards", lambda *args, **kwargs: {key: card}
    )

    def fail_generation(*args):
        args[-1]["content"] = "Partial output"
        raise RuntimeError("provider failed")

    monkeypatch.setattr(review_writer, "generate_related_work_from_cards", fail_generation)
    legacy = Mock()
    monkeypatch.setattr(review_writer, "_generate_single_batch", legacy)
    with pytest.raises(RuntimeError, match="provider failed"):
        asyncio.run(review_writer.write_review(task.id, [0]))
    assert task.results["custom_reviews"][0]["status"] == "failed"
    assert task.results["custom_reviews"][0]["content"] == "Partial output"
    legacy.assert_not_called()


def test_planner_maps_display_indices_to_stable_ids_for_98_cards(monkeypatch):
    from backend.prompt_log import get_log

    task = task_manager.create_task(keywords=["VLA memory"])
    cards = [{"paper_id": f"hash-{i:03d}", "title": f"Paper {i}"} for i in range(1, 99)]
    response = {
        "inferred_title": "VLA memory",
        "included_references": ["22", "27"],
        "citation_roles": {"22": "memory architecture", "27": "planner comparison"},
    }
    llm = Mock()
    llm.invoke.return_value = SimpleNamespace(content=json.dumps(response))
    monkeypatch.setattr(review_writer, "get_llm", lambda: llm)
    plan = review_writer.plan_related_work(cards, "", "", "VLA memory", task.id)
    assert plan["included_references"] == ["hash-022", "hash-027"]
    assert plan["citation_roles"] == {
        "hash-022": "memory architecture",
        "hash-027": "planner comparison",
    }
    prompt = llm.invoke.call_args.args[0][1].content
    assert "### [22] Paper 22\n- Stable paper_id: hash-022" in prompt
    assert len(plan["excluded_references"]) == 96
    assert get_log(task.id)[0]["status"] == "success"


@pytest.mark.parametrize("ids", [["unknown"], ["99"], ["22", "hash-022"], []])
def test_planner_rejects_unknown_duplicate_or_empty_selections(ids, monkeypatch):
    from backend.prompt_log import get_log

    task = task_manager.create_task(keywords=["VLA memory"])
    cards = [{"paper_id": f"hash-{i:03d}", "title": f"Paper {i}"} for i in range(1, 99)]
    llm = Mock()
    llm.invoke.return_value = SimpleNamespace(
        content=json.dumps(
            {
                "inferred_title": "VLA memory",
                "included_references": ids,
            }
        )
    )
    monkeypatch.setattr(review_writer, "get_llm", lambda: llm)
    with pytest.raises(review_writer.PlannerValidationError):
        review_writer.plan_related_work(cards, "", "", "VLA memory", task.id)
    assert get_log(task.id)[0]["status"] == "error"


def test_planner_parse_failure_does_not_choose_unrelated_fallback(monkeypatch):
    from backend.prompt_log import get_log

    task = task_manager.create_task(keywords=["VLA memory"])
    llm = Mock()
    llm.invoke.return_value = SimpleNamespace(content="This is not a planner JSON object")
    monkeypatch.setattr(review_writer, "get_llm", lambda: llm)
    with pytest.raises(review_writer.PlannerValidationError):
        review_writer.plan_related_work(
            [{"paper_id": "stable", "title": "Paper"}], "", "", "VLA memory", task.id
        )
    assert get_log(task.id)[0]["status"] == "error"


def test_selected_late_cards_reach_writer_without_first_eight_substitution(monkeypatch):
    papers = [
        PaperInfo(title=f"Paper {i}", url=f"https://example.test/paper/{i}") for i in range(1, 99)
    ]
    task = task_manager.create_task(keywords=["VLA memory"], papers=papers)
    cards = {
        reference_card.paper_hash(paper.model_dump()): {
            "paper_id": reference_card.paper_hash(paper.model_dump()),
            "title": paper.title,
        }
        for paper in papers
    }
    monkeypatch.setattr(reference_card, "_find_pdf_for_paper", lambda *args: "cached.pdf")
    monkeypatch.setattr(review_writer, "extract_reference_cards", lambda *args, **kwargs: cards)
    monkeypatch.setattr(
        review_writer,
        "plan_related_work",
        lambda *args: {
            "inferred_title": "VLA memory",
            "included_references": ["22", "27"],
        },
    )
    seen = []

    def generate(selected, *args):
        seen.extend(card["title"] for card in selected)
        args[-1]["content"] = "Memory models [1]."
        return args[-1]["content"]

    monkeypatch.setattr(review_writer, "generate_related_work_from_cards", generate)
    result = asyncio.run(review_writer.write_review(task.id, list(range(98))))
    review = task.results["custom_reviews"][0]
    assert review["id"] == result
    assert seen == ["Paper 22", "Paper 27"]
    assert review["included_reference_ids"] == [
        reference_card.paper_hash(papers[21].model_dump()),
        reference_card.paper_hash(papers[26].model_dump()),
    ]
    assert "[2]: not used" in review["citation_usage"]


def test_unknown_planner_id_marks_review_failed_and_never_calls_writer(review_fixture, monkeypatch):
    task, key, card = review_fixture
    monkeypatch.setattr(reference_card, "_find_pdf_for_paper", lambda *args: "cached.pdf")
    monkeypatch.setattr(
        review_writer, "extract_reference_cards", lambda *args, **kwargs: {key: card}
    )
    monkeypatch.setattr(
        review_writer,
        "plan_related_work",
        lambda *args: {
            "inferred_title": "VLA memory",
            "included_references": ["unknown"],
        },
    )
    writer = Mock()
    legacy = Mock()
    monkeypatch.setattr(review_writer, "generate_related_work_from_cards", writer)
    monkeypatch.setattr(review_writer, "_generate_single_batch", legacy)
    with pytest.raises(RuntimeError, match="Unknown planner reference ID"):
        asyncio.run(review_writer.write_review(task.id, [0]))
    review = task.results["custom_reviews"][0]
    assert review["status"] == "failed"
    assert "Unknown planner reference ID" in review["error"]
    writer.assert_not_called()
    legacy.assert_not_called()


def test_citation_checklist_does_not_count_as_body_usage():
    cards = [{"paper_id": f"p{i}"} for i in range(1, 4)]
    usage = review_writer._citation_usage(
        "Models [1, 3].\n\n**Citation Usage:**\n- [1]: used\n- [2]: used\n- [3]: used", cards, {}
    )
    assert "[1]: used" in usage
    assert "[2]: not used" in usage
    assert "[3]: used" in usage


def test_stream_failure_preserves_partial_text_and_closes_prompt_log(monkeypatch):
    from backend.prompt_log import get_log

    task = task_manager.create_task(keywords=["VLA memory"])
    llm = Mock()

    def stream(*args):
        yield SimpleNamespace(content="Partial evidence")
        raise RuntimeError("stream interrupted")

    llm.stream.side_effect = stream
    accumulated = {"content": "", "review_id": "job"}
    with pytest.raises(RuntimeError, match="stream interrupted"):
        review_writer._stream_and_record(task.id, "Writer", llm, [], accumulated)
    assert accumulated["content"] == "Partial evidence"
    log = get_log(task.id)
    assert len(log) == 1
    assert log[0]["status"] == "error"
    assert "Partial evidence" in log[0]["response_preview"]


def test_existing_job_id_and_metadata_are_reused(review_fixture, monkeypatch):
    task, key, card = review_fixture
    task_manager.update_task(
        task.id,
        results={
            "custom_reviews": [
                {
                    "id": "existing-job",
                    "created_at": "2026-10-29T01:00:00",
                    "status": "queued",
                    "mode": "related_work",
                    "language": "zh",
                    "template_id": "journal-template",
                    "stages": [{"key": "preparation", "label": "准备", "status": "running"}],
                }
            ]
        },
    )
    monkeypatch.setattr(reference_card, "_find_pdf_for_paper", lambda *args: "cached.pdf")
    monkeypatch.setattr(
        review_writer, "extract_reference_cards", lambda *args, **kwargs: {key: card}
    )

    def generate(*args):
        args[-1]["content"] = "Related Work [1]."
        return args[-1]["content"]

    monkeypatch.setattr(review_writer, "generate_related_work_from_cards", generate)
    result = asyncio.run(review_writer.write_review(task.id, [0], review_id="existing-job"))
    reviews = task.results["custom_reviews"]
    assert result == "existing-job" and len(reviews) == 1
    assert reviews[0]["created_at"] == "2026-10-29T01:00:00"
    assert reviews[0]["mode"] == "related_work" and reviews[0]["language"] == "zh"
    assert reviews[0]["template_id"] == "journal-template"
    assert reviews[0]["stages"][0]["key"] == "preparation"


def test_cancel_after_download_does_not_start_card_extraction(review_fixture, monkeypatch):
    from backend import download_agent

    task, _, _ = review_fixture
    cancel_event = threading.Event()
    monkeypatch.setattr(download_agent, "download_papers", lambda *args: cancel_event.set())
    extract = Mock()
    monkeypatch.setattr(review_writer, "extract_reference_cards", extract)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(review_writer.write_review(task.id, [0], cancel_event=cancel_event))
    extract.assert_not_called()

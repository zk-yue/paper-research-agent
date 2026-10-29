import asyncio
import json

import pytest

from backend import reference_card, review_service, review_writer, survey_writer
from backend.routes.reviews import export_review, write_review
from backend.schemas import PaperInfo, WriteReviewRequest
from backend.task_manager import task_manager
from backend.task_paths import task_metadata_file


class FakeModel:
    model = "test-model"


def test_start_is_immediate_and_failure_resumes_same_durable_record(monkeypatch):
    task = task_manager.create_task(keywords=["memory"], papers=[PaperInfo(title="One")])
    monkeypatch.setattr(review_service, "get_llm", FakeModel)
    attempts = []

    async def generate(task, review, root, invoke, update, cancelled):
        attempts.append(review["id"])
        if len(attempts) == 1:
            update(
                sections=[
                    {"id": "s01", "title": "Saved", "status": "done", "content": "Saved [1]."}
                ],
                content="Saved [1].",
            )
            raise RuntimeError("second section failed")
        assert review["sections"][0]["status"] == "done"
        update(content="Complete [1].", stage="complete")

    monkeypatch.setattr(survey_writer, "generate_survey", generate)

    async def scenario():
        response = await write_review(task.id, WriteReviewRequest())
        rid = response["review_id"]
        persisted = json.loads(task_metadata_file(task.id).read_text())
        assert persisted["results"]["custom_reviews"][0]["status"] == "queued"
        assert attempts == []
        job = review_service._jobs[(task.id, rid)]
        await job
        await asyncio.sleep(0)
        review = review_service.get_review(task.id, rid)
        assert review["status"] == "failed" and review["resumable"]
        assert review["content"] == "Saved [1]."
        assert review_service.resume_review(task.id, rid) == rid
        await review_service._jobs[(task.id, rid)]
        await asyncio.sleep(0)
        assert review["status"] == "done"
        assert len(task.results["custom_reviews"]) == 1
        exported = await export_review(task.id, rid)
        assert exported.body == b"Complete [1]."

    asyncio.run(scenario())


def test_cancel_preserves_completed_sections_and_blocks_duplicate_jobs(monkeypatch):
    task = task_manager.create_task(keywords=["memory"], papers=[PaperInfo(title="One")])
    monkeypatch.setattr(review_service, "get_llm", FakeModel)

    async def scenario():
        started = asyncio.Event()

        async def generate(task, review, root, invoke, update, cancelled):
            update(content="Completed part")
            started.set()
            while not cancelled():
                await asyncio.sleep(0.001)
            raise asyncio.CancelledError

        monkeypatch.setattr(survey_writer, "generate_survey", generate)
        rid = review_service.start_review(task.id, [])
        await started.wait()
        with pytest.raises(RuntimeError, match="已有综述"):
            review_service.start_review(task.id, [])
        review_service.cancel_review(task.id, rid)
        await review_service._jobs[(task.id, rid)]
        await asyncio.sleep(0)
        review = review_service.get_review(task.id, rid)
        assert review["status"] == "cancelled"
        assert review["content"] == "Completed part" and review["resumable"]
        assert not review_service._jobs

    asyncio.run(scenario())


def test_restart_recovers_incomplete_review_without_discarding_text():
    task = task_manager.create_task(
        keywords=["memory"],
        results={
            "custom_reviews": [
                {
                    "id": "active",
                    "status": "running",
                    "mode": "survey",
                    "content": "saved",
                    "stage": "writing",
                },
                {"id": "finished", "status": "done", "mode": "survey", "content": "complete"},
            ]
        },
    )
    review_service.recover_reviews()
    active, finished = task.results["custom_reviews"]
    assert active["status"] == "interrupted" and active["content"] == "saved"
    assert active["resumable"]
    assert finished["status"] == "done" and finished["content"] == "complete"


def test_snapshot_freezes_papers_screening_and_options_across_resume(monkeypatch):
    original_abstract = "Original evidence about memory"
    task = task_manager.create_task(
        keywords=["memory"],
        topic="Original topic",
        papers=[PaperInfo(title="Original paper", url="https://example.test/original")],
        results={
            "screening": {"included": [{"title": "Original paper", "abstract": original_abstract}]},
            "custom_reviews": [{"id": "old-review", "content": "OLDER BODY" * 1000}],
        },
    )
    monkeypatch.setattr(review_service, "get_llm", FakeModel)
    attempts = []

    async def generate(source_task, review, root, invoke, update, cancelled):
        attempts.append(source_task)
        assert source_task.id == task.id
        assert source_task.topic == "Original topic"
        assert source_task.papers[0].title == "Original paper"
        assert source_task.results["screening"]["included"][0]["abstract"] == original_abstract
        assert review["paper_indices"] == [0] and review["instructions"] == "Original instructions"
        assert "custom_reviews" not in source_task.results
        if len(attempts) == 1:
            update(content="Saved original citation [1].")
            raise RuntimeError("retry section")
        update(content="Completed original citation [1].")

    monkeypatch.setattr(survey_writer, "generate_survey", generate)

    async def scenario():
        rid = review_service.start_review(task.id, [], instructions="Original instructions")
        snapshot_path = review_service.review_root(task.id, rid) / "input.json"
        snapshot_text = snapshot_path.read_text()
        assert "OLDER BODY" not in snapshot_text and "custom_reviews" not in snapshot_text
        task_manager.update_task(
            task.id, papers=[PaperInfo(title="Replacement")], topic="New topic"
        )
        task.results["screening"]["included"][0]["abstract"] = "Changed evidence"
        review_service.get_review(task.id, rid)["instructions"] = "Changed instructions"
        await review_service._jobs[(task.id, rid)]
        await asyncio.sleep(0)
        assert review_service.get_review(task.id, rid)["status"] == "failed"
        review_service.resume_review(task.id, rid)
        await review_service._jobs[(task.id, rid)]
        await asyncio.sleep(0)
        assert len(attempts) == 2 and attempts[0] is not attempts[1]
        assert snapshot_path.read_text() == snapshot_text
        assert review_service.get_review(task.id, rid)["status"] == "done"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("filename", "content", "error"),
    [
        ("input.json", "{broken", "input.json"),
        ("input.json", None, "input.json"),
        ("input.json", '{"version":1}', "快照校验失败"),
        ("model-calls.json", "{broken", "model-calls.json"),
        ("model-calls.json", "{}", "必须为调用记录列表"),
    ],
)
def test_initialization_checkpoint_errors_are_persisted_as_failed(
    filename, content, error, monkeypatch
):
    task = task_manager.create_task(keywords=["memory"], papers=[PaperInfo(title="One")])
    monkeypatch.setattr(review_service, "get_llm", FakeModel)
    called = []

    async def generate(*args):
        called.append(True)

    monkeypatch.setattr(survey_writer, "generate_survey", generate)

    async def scenario():
        rid = review_service.start_review(task.id, [])
        root = review_service.review_root(task.id, rid)
        checkpoint = root / filename
        if content is None:
            checkpoint.unlink()
        else:
            checkpoint.write_text(content)
        review_service.update_review(task.id, rid, content="Saved draft")
        await review_service._jobs[(task.id, rid)]
        await asyncio.sleep(0)
        review = review_service.get_review(task.id, rid)
        persisted = json.loads((root / "state.json").read_text())
        assert review["status"] == persisted["status"] == "failed"
        assert error in review["error"] and not review["resumable"]
        assert review["content"] == "Saved draft" and not called
        if content is not None:
            assert checkpoint.read_text() == content, "Invalid checkpoint must not be overwritten"

    asyncio.run(scenario())


@pytest.mark.parametrize("fail_writer", [False, True])
def test_related_work_uses_snapshot_and_persists_real_stages_and_partial_text(
    fail_writer, monkeypatch
):
    task = task_manager.create_task(
        keywords=["memory"], papers=[PaperInfo(title="Unselected"), PaperInfo(title="Selected")]
    )
    original = task.papers[1].model_dump()
    key = reference_card.paper_hash(original)
    monkeypatch.setattr(review_service, "get_llm", FakeModel)
    monkeypatch.setattr(reference_card, "_find_pdf_for_paper", lambda *args: "cached.pdf")
    stages = []

    def current_stage():
        review = task.results["custom_reviews"][0]
        saved = json.loads(
            (review_service.review_root(task.id, review["id"]) / "state.json").read_text()
        )
        assert saved["stage"] == review["stage"]
        assert saved["progress"]["total"] == 1
        return saved["stage"]

    def extract(task_id, papers_with_idx, *args, **kwargs):
        assert task_id == task.id
        assert papers_with_idx[0][0] == 1 and papers_with_idx[0][1]["title"] == "Selected"
        stages.append(current_stage())
        return {key: {"paper_id": key, "title": "Selected"}}

    def plan(*args):
        stages.append(current_stage())
        return {"inferred_title": "Memory models", "included_references": [key]}

    def generate(selected, *args):
        stages.append(current_stage())
        assert selected[0]["title"] == "Selected"
        args[-1]["content"] = "Original paper [1]."
        if fail_writer:
            raise RuntimeError("provider failed mid-stream")
        return args[-1]["content"]

    monkeypatch.setattr(review_writer, "extract_reference_cards", extract)
    monkeypatch.setattr(review_writer, "plan_related_work", plan)
    monkeypatch.setattr(review_writer, "generate_related_work_from_cards", generate)

    async def scenario():
        response = await write_review(
            task.id, WriteReviewRequest(paper_indices=[1], mode="related_work")
        )
        rid = response["review_id"]
        assert stages == []
        task_manager.update_task(task.id, papers=[PaperInfo(title="Replacement")])
        await review_service._jobs[(task.id, rid)]
        await asyncio.sleep(0)
        review = review_service.get_review(task.id, rid)
        assert review["status"] == ("failed" if fail_writer else "done")
        assert review["paper_titles"] == ["Selected"]
        assert review["content"] == "Original paper [1]."
        assert stages == ["evidence", "planning", "writing"]
        assert [s["key"] for s in review["stages"]] == [
            "evidence",
            "planning",
            "writing",
            "auditing",
            "complete",
        ]
        if fail_writer:
            assert review["stage"] == "writing" and "mid-stream" in review["error"]
        else:
            assert review["stage"] == "complete" and review["progress"]["completed"] == 1

    asyncio.run(scenario())

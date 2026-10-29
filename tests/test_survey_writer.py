import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from backend import survey_evidence, survey_writer
from backend.schemas import PaperInfo, TaskInfo


def test_checkpoint_reuses_verified_chapters_and_keeps_global_reference_ids(tmp_path, monkeypatch):
    records = [
        {
            "citation_id": i,
            "paper_key": f"key{i}",
            "source_hash": f"hash{i}",
            "title": f"Paper {i}",
            "url": f"https://example.org/{i}",
            "year": "2026",
            "source_level": "fulltext",
            "source_format": "pdf",
            "abstract": "",
            "card": {},
            "passages": [
                {
                    "id": f"R{i}-p1",
                    "page": 1,
                    "text": f"Source evidence for paper {i} is unique and exact: $8\\times$.",
                }
            ],
        }
        for i in (1, 2)
    ]
    monkeypatch.setattr(survey_writer, "prepare_evidence", lambda *a, **k: copy.deepcopy(records))
    monkeypatch.setattr(
        survey_writer, "select_passages", lambda record, *a, **k: record["passages"]
    )
    task = SimpleNamespace(topic="Memory", keywords=["Memory"], year_from=2021)
    review = {"paper_indices": [0, 1], "instructions": "", "language": "zh"}
    plan = {
        "title": "Memory survey",
        "scope": "Memory mechanisms",
        "sections": [
            {
                "id": f"s0{i}",
                "title": f"Group {i}",
                "focus": "mechanism",
                "reference_ids": [i],
                "target_chars": 1200,
            }
            for i in (1, 2)
        ],
    }
    calls = []
    fail = {"value": True}

    async def invoke(system, user, label):
        calls.append(label)
        if label == "outline":
            if calls.count(label) == 1:
                return json.dumps({**plan, "sections": None})
            return json.dumps(plan)
        if label.startswith("section-"):
            i = int(label[-1])
            if i == 2 and fail["value"]:
                raise RuntimeError("provider interruption")
            return json.dumps(
                {
                    "content": f"Mechanism of paper {i} [{i}].",
                    "evidence": [
                        {
                            "citation_id": i,
                            "passage_id": f"R{i}-p1",
                            "quote": records[i - 1]["passages"][0]["text"],
                            "claim": "Mechanism",
                        }
                    ],
                }
            )
        if label == "quality-audit":
            return json.dumps({"summary": "Verified", "issues": []})
        if label == "final-audit":
            if calls.count(label) == 1:
                return json.dumps({"summary": "Invalid schema", "issues": [{}]})
            if calls.count(label) == 2:
                return json.dumps(
                    {
                        "summary": "Needs a correction",
                        "issues": [
                            {
                                "severity": "error",
                                "section_id": "discussion",
                                "reference_ids": [1],
                                "description": "Unsupported additional claim",
                                "correction": "Keep discussion within the original source claims",
                            }
                        ],
                    }
                )
            return json.dumps(
                {"summary": "Synthesis checked against chapter evidence", "issues": []}
            )
        if label == "synthesis":
            return json.dumps(
                {
                    "introduction": "## 导论\n\n" + "Background [1]. " * 30,
                    "discussion": (
                        "Supported comparison [1, 2]. "
                        if calls.count(label) > 1
                        else "Comparison [1, 2]. "
                    )
                    * 30,
                }
            )
        raise AssertionError(label)

    states = []

    def update(patch=None, **kwargs):
        states.append(copy.deepcopy({**(patch or {}), **kwargs}))

    async def scenario():
        with pytest.raises(RuntimeError, match="章节未完成"):
            await survey_writer.generate_survey(
                task, review, tmp_path, invoke, update, lambda: False
            )
        assert (tmp_path / "sections/s01.json").exists()
        assert not (tmp_path / "review.md").exists()
        # Preserve a completed provider response whose only defect was redundant
        # JSON escaping; resuming should revalidate and recover it without a call.
        quote = records[1]["passages"][0]["text"].replace("\\times", "\\\\times")
        (tmp_path / "section-s02-invalid-0.json").write_text(
            json.dumps(
                {
                    "raw": json.dumps(
                        {
                            "content": "Mechanism of paper 2 [2].",
                            "evidence": [
                                {
                                    "citation_id": 2,
                                    "passage_id": "R2-p1",
                                    "quote": quote,
                                    "claim": "Mechanism",
                                }
                            ],
                        }
                    ),
                    "issues": ["Repeated escaping"],
                }
            )
        )
        fail["value"] = False
        content = await survey_writer.generate_survey(
            task, review, tmp_path, invoke, update, lambda: False
        )
        assert calls.count("outline") == 2
        assert calls.count("section-s01") == 1
        assert calls.count("section-s02") == 1
        recovered = json.loads((tmp_path / "sections/s02.json").read_text())
        assert recovered["evidence"][0]["original_quote"] == quote
        assert calls.count("synthesis") == 2
        assert calls.count("final-audit") == 3
        assert content.count("## 参考文献") == 1
        assert "## 导论\n" not in content
        assert "## 1. 导论与范围" in content
        assert "https://example.org/2" in content
        assert "Group 1" in content and "Group 2" in content
        assert states[-1]["coverage"]["cited"] == 2
        records[0]["source_hash"] = "changed"
        with pytest.raises(ValueError, match="原文资料或配置已变化"):
            await survey_writer.generate_survey(
                task, review, tmp_path, invoke, update, lambda: False
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("sections", [None, [None], [{"reference_ids": None}], "invalid"])
def test_malformed_outline_reports_validation_errors_without_crashing(sections):
    assert survey_writer._plan_issues({"sections": sections}, [{"citation_id": 1}])


@pytest.mark.parametrize(
    "issue", [None, {}, {"severity": "ERROR"}, {"severity": {}, "section_id": []}]
)
def test_malformed_audits_cannot_appear_successful(issue):
    assert survey_writer._audit_issues({"summary": "Result", "issues": [issue]}, {"s01"}, {1})


def test_metadata_only_paper_fails_before_any_model_call(tmp_path, monkeypatch):
    monkeypatch.setattr(
        survey_writer,
        "prepare_evidence",
        lambda *a, **k: [{"citation_id": 1, "source_level": "metadata"}],
    )

    async def invoke(*args):
        raise AssertionError("Metadata-only evidence cannot support writing")

    with pytest.raises(ValueError, match="只有元数据"):
        asyncio.run(
            survey_writer.generate_survey(
                SimpleNamespace(topic="Memory", keywords=[]),
                {"paper_indices": [0]},
                tmp_path,
                invoke,
                lambda *a, **k: None,
                lambda: False,
            )
        )


def _uncached_html_task():
    return TaskInfo(
        id="cold-survey",
        status="completed",
        topic="Cloth manipulation",
        keywords=["cloth"],
        papers=[
            PaperInfo(
                title="Cloth dynamics",
                authors="A. Researcher",
                year="2026",
                url="https://arxiv.org/abs/2601.12345v1",
            )
        ],
    )


def test_survey_prepares_uncached_html_before_calling_model(tmp_path, monkeypatch):
    """Exercise the real writer → evidence → HTTP boundary, including wait(0)."""
    requests = []

    class Response:
        status_code = 200
        headers = {"Content-Type": "text/html"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, chunk_size):
            yield (
                '<article class="ltx_document"><section class="ltx_section" id="S1">'
                '<h2 class="ltx_title">Method</h2><p>'
                + "Our action-conditioned model predicts cloth motion. "
                * 40
                + '</p></section><section class="ltx_section" id="S2">'
                '<h2 class="ltx_title">Experiments</h2><p>'
                + "We evaluate prediction error on held-out cloth trajectories. " * 20
                + "</p></section></article>"
            ).encode()

    def get(url, **kwargs):
        requests.append(url)
        return Response()

    monkeypatch.setattr(survey_evidence.requests, "get", get)
    monkeypatch.setattr(survey_evidence, "_HTML_INTERVAL", 0)
    monkeypatch.setattr(survey_evidence, "_HTML_LAST_REQUEST", 0)

    async def invoke(system, user, label):
        assert label == "outline"
        assert json.loads(user)["papers"][0]["source_level"] == "fulltext"
        raise RuntimeError("Reached model after preparing HTML")

    with pytest.raises(RuntimeError, match="Reached model after preparing HTML"):
        asyncio.run(
            survey_writer.generate_survey(
                _uncached_html_task(),
                {"paper_indices": [0]},
                tmp_path / "review",
                invoke,
                lambda *a, **k: None,
                lambda: False,
            )
        )
    assert requests == ["https://arxiv.org/html/2601.12345v1"]
    evidence = json.loads((tmp_path / "review/evidence.json").read_text())
    assert evidence[0]["source_format"] == "html"


def test_survey_cancels_during_html_rate_limit_before_request(tmp_path, monkeypatch):
    cancelled = False
    waits = []

    def sleep(duration):
        nonlocal cancelled
        waits.append(duration)
        cancelled = True

    monkeypatch.setattr(survey_writer.time, "sleep", sleep)
    monkeypatch.setattr(survey_evidence, "_HTML_INTERVAL", 3)
    monkeypatch.setattr(survey_evidence, "_HTML_LAST_REQUEST", survey_writer.time.monotonic())
    monkeypatch.setattr(
        survey_evidence.requests, "get", lambda *a, **k: pytest.fail("Cancelled before HTTP")
    )

    async def invoke(*args):
        pytest.fail("Cancelled before any model call")

    with pytest.raises(survey_evidence.EvidenceCancelled):
        asyncio.run(
            survey_writer.generate_survey(
                _uncached_html_task(),
                {"paper_indices": [0]},
                tmp_path / "review",
                invoke,
                lambda *a, **k: None,
                lambda: cancelled,
            )
        )
    assert len(waits) == 1
    assert 0 < waits[0] <= 0.05
    assert not list(tmp_path.rglob("html-*.json")), "Cancellation must not cache an HTTP failure"

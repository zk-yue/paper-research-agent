"""Supplemental evaluation preserves discovery provenance and evidence boundaries."""

import asyncio
import copy
import json
from argparse import Namespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.retrieval import SearchResults
from scripts import refine_search_evaluation as refine


def paper(title, status="included", score=5, abstract="A robot uses memory to plan actions."):
    return {
        "title": title,
        "abstract": abstract,
        "screening_status": status,
        "relevance_score": score,
        "published_date": "2025-01-01",
        "matched_queries": ["original query"],
    }


def base_result():
    return {
        "evaluation": {
            "topic": "robot memory",
            "year_from": 2021,
            "max_rounds": 3,
            "started_at": "2026-10-29T01:00:00+00:00",
            "completed_at": "2026-10-29T01:30:00+00:00",
        },
        "topic_plan": {"seed_queries": ["original query"], "sub_directions": ["memory"]},
        "screening": {
            "included": [paper("Direct")],
            "supporting": [paper("Background", "supporting", 3)],
            "pending": [paper("Uncertain", "pending", 0, "")],
            "excluded": [paper("Irrelevant", "excluded", 1)],
        },
        "papers": [paper("Direct")],
        "iteration_log": [{"round": 1, "queries": ["original query"]}],
    }


def test_prepare_screens_only_new_or_richer_pending_and_preserves_base():
    base = base_result()
    original = copy.deepcopy(base)
    supplement = {
        "papers": [
            {**paper("Direct"), "matched_queries": ["new query"]},
            paper("Uncertain"),
            paper("New candidate"),
            paper("Irrelevant"),
            {**paper("Too old"), "published_date": "2020-01-01"},
        ]
    }
    records, indices = refine.prepare_records(base, [supplement])
    assert {records[i]["title"] for i in indices} == {"Uncertain", "New candidate"}
    assert len(records) == 5
    assert records[0]["matched_queries"] == ["original query", "new query"]
    assert base == original


def test_recheck_requires_actual_response_and_cannot_inject_an_undiscovered_paper(tmp_path):
    verdict = {
        **paper("Direct", "supporting", 3),
        "relevance_reason": "Only transferable",
        "relevance_evidence": "A robot uses memory to plan actions.",
        "relevance_match_scope": "transferable",
    }
    response = {
        "id": "p0",
        "decision": "include",
        "score": 3,
        "reason": "Only transferable",
        "evidence": verdict["relevance_evidence"],
    }
    document = {
        "completed_at": "2026-10-29T02:00:00+00:00",
        "audit": [verdict, paper("Undiscovered")],
        "checks": [{"title": "Undiscovered", "passed": True}],
        "llm_calls": [
            {
                "messages": [{"content": "Direct: A robot uses memory to plan actions."}],
                "response": json.dumps([response]),
            }
        ],
    }
    path = tmp_path / "recheck.json"
    path.write_text(json.dumps(document))
    records = [paper("Direct")]
    applied, _ = refine.apply_rechecks(records, set(), [(path, document)])
    assert len(records) == 1 and len(applied) == 1
    assert records[0]["screening_status"] == "supporting"
    assert records[0]["matched_queries"] == ["original query"]
    document["llm_calls"] = []
    assert not refine.verified_recheck(verdict, records[0], document)


def test_refinement_refuses_incomplete_base_without_creating_output(tmp_path):
    args = Namespace(base=tmp_path / "missing", output=tmp_path / "output")
    with pytest.raises(ValueError, match="absent"):
        asyncio.run(refine.run(args))
    assert not args.output.exists()


@pytest.mark.parametrize("skip_references", [False, True])
def test_refinement_appends_round_and_regenerates_summary_only_from_direct_evidence(
    tmp_path, monkeypatch, skip_references
):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    original = base_result()
    if skip_references:
        prior_citations = tmp_path / "prior-citations.json"
        prior_citations.write_text('{"papers": []}')
        original["refinement"] = {
            "citation_artifact": {
                "path": str(prior_citations),
                "sha256": refine.sha256(prior_citations),
            }
        }
    (base_dir / "result.json").write_text(json.dumps(original))
    (base_dir / "progress.json").write_text(json.dumps({"status": "completed"}))
    supplement_path = tmp_path / "supplement.json"
    supplement_path.write_text(
        json.dumps(
            {
                "topic": "robot memory",
                "year_from": 2021,
                "completed_at": "2026-10-29T02:00:00+00:00",
                "queries": ["robot experience memory"],
                "papers": [paper("New candidate")],
                "diagnostics": [{"source": "arxiv", "status": "success", "truncated": True}],
                "warnings": ["capped"],
            }
        )
    )
    model = Mock(model="gpt-6.1-sol", reasoning_effort="medium")
    monkeypatch.setenv("LLM_PROVIDER", "codex")
    monkeypatch.setattr(refine, "get_llm", lambda: model)
    references = Mock(return_value=SearchResults())
    monkeypatch.setattr(refine, "retrieve_references", references)

    async def screen(candidates, topic, invoke, log, round_number, *, topic_plan, audit, on_batch):
        audit.extend(
            [{**p, "screening_status": "included", "relevance_score": 4} for p in candidates]
        )
        on_batch(audit)
        return audit

    monkeypatch.setattr(refine, "filter_papers_with_reason", screen)
    summary = AsyncMock(return_value="Summary of directly relevant evidence")
    monkeypatch.setattr(refine, "generate_deep_summary", summary)
    args = Namespace(
        base=base_dir,
        supplement=[supplement_path],
        recheck=[],
        planning_artifact=[],
        output=tmp_path / "final",
        skip_references=skip_references,
    )
    result = asyncio.run(refine.run(args))
    assert {p["title"] for p in result["papers"]} == {"Direct", "New candidate"}
    assert result["topic_plan"] == original["topic_plan"]
    assert result["iteration_log"][0] == original["iteration_log"][0]
    assert result["iteration_log"][-1]["round"] == 4
    assert result["iteration_log"][-1]["queries"] == ["robot experience memory"] + (
        [] if skip_references else ["backward citation search"]
    )
    if skip_references:
        references.assert_not_called()
        assert (
            result["refinement"]["citation_artifact"] == original["refinement"]["citation_artifact"]
        )
        assert not (args.output / "citation-retrieval.json").exists()
    else:
        references.assert_called_once()
        assert all(p["relevance_score"] >= 4 for p in references.call_args.args[0])
    assert result["coverage_status"] == "bounded_results"
    assert result["refinement"]["base_screening"] == original["screening"]
    assert json.loads((base_dir / "result.json").read_text()) == original
    assert all(p["relevance_score"] >= 4 for p in summary.call_args.args[1])
    with pytest.raises(ValueError, match="overwrite"):
        asyncio.run(refine.run(args))


def test_rescreen_main_only_keeps_other_groups_and_supports_checkpoint_resume(
    tmp_path, monkeypatch
):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    original = base_result()
    original["screening"]["included"][0]["discovery_round"] = 1
    (base_dir / "result.json").write_text(json.dumps(original))
    (base_dir / "progress.json").write_text(json.dumps({"status": "completed"}))
    monkeypatch.setenv("LLM_PROVIDER", "codex")
    monkeypatch.setattr(
        refine, "get_llm", lambda: Mock(model="gpt-6.1-sol", reasoning_effort="medium")
    )
    references = Mock(side_effect=AssertionError("No new citation hop during rescreen"))
    monkeypatch.setattr(refine, "retrieve_references", references)
    seen = []

    async def screen(candidates, topic, invoke, log, round_number, *, topic_plan, audit, on_batch):
        seen.extend(candidates)
        assert [p["title"] for p in candidates] == ["Direct"]
        assert "relevance_score" not in candidates[0]
        assert "relevance_reason" not in candidates[0]
        audit.extend(
            [
                {
                    **p,
                    "screening_status": "supporting",
                    "relevance_score": 3,
                    "discovery_round": round_number,
                }
                for p in candidates
            ]
        )
        on_batch(audit)
        return []

    monkeypatch.setattr(refine, "filter_papers_with_reason", screen)
    summary = AsyncMock(return_value="not needed")
    monkeypatch.setattr(refine, "generate_deep_summary", summary)
    args = Namespace(
        base=base_dir,
        supplement=[],
        recheck=[],
        planning_artifact=[],
        output=tmp_path / "v3",
        skip_references=False,
        rescreen_included=True,
        resume_screening=None,
    )
    result = asyncio.run(refine.run(args))
    assert result["papers"] == []
    assert result["iteration_log"] == original["iteration_log"]
    assert result["screening"]["counts"] == {
        "included": 0,
        "supporting": 2,
        "excluded": 1,
        "pending": 1,
    }
    assert (
        next(p for p in result["screening"]["supporting"] if p["title"] == "Direct")[
            "discovery_round"
        ]
        == 1
    )
    assert result["refinement"]["rescreened_included_count"] == 1
    references.assert_not_called()
    summary.assert_not_called()
    checkpoint = args.output / "screening-checkpoint.json"
    assert json.loads(checkpoint.read_text())["records"][0]["title"] == "Direct"
    args.output = tmp_path / "restored"
    args.resume_screening = checkpoint
    result = asyncio.run(refine.run(args))
    assert len(seen) == 1
    assert result["refinement"]["resumed_screening_count"] == 1
    assert result["screening"]["counts"]["supporting"] == 2


def test_rescreen_handles_batches_finishing_out_of_order(tmp_path, monkeypatch):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    original = base_result()
    original["screening"]["included"] = [
        {**paper("Direct A"), "discovery_round": 1},
        {**paper("Direct B"), "discovery_round": 2},
    ]
    original["papers"] = original["screening"]["included"]
    (base_dir / "result.json").write_text(json.dumps(original))
    monkeypatch.setenv("LLM_PROVIDER", "codex")
    monkeypatch.setattr(
        refine, "get_llm", lambda: Mock(model="gpt-6.1-sol", reasoning_effort="medium")
    )
    monkeypatch.setattr(refine, "generate_deep_summary", AsyncMock(return_value="Summary"))

    async def screen(candidates, topic, invoke, log, round_number, *, topic_plan, audit, on_batch):
        # This is the real streaming audit contract: completed batches can
        # append in a different order than candidate discovery.
        for p in reversed(candidates):
            record = {
                **p,
                "screening_status": "included",
                "relevance_score": 4,
                "discovery_round": round_number,
            }
            audit.append(record)
            on_batch([record])
        return audit

    monkeypatch.setattr(refine, "filter_papers_with_reason", screen)
    args = Namespace(
        base=base_dir,
        supplement=[],
        recheck=[],
        planning_artifact=[],
        output=tmp_path / "out",
        rescreen_included=True,
        skip_references=True,
    )
    result = asyncio.run(refine.run(args))
    assert {p["title"]: p["discovery_round"] for p in result["papers"]} == {
        "Direct A": 1,
        "Direct B": 2,
    }

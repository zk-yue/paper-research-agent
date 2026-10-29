"""Evidence-based screening regressions; no network or paid model calls."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage

from backend import deep_search_agent as deep


def response(value):
    return AIMessage(content=json.dumps(value))


def decision(pid, evidence, score=4, scope="subtopic"):
    return {
        "id": pid,
        "decision": "include" if score >= 3 else "exclude",
        "score": score,
        "match_scope": scope,
        "reason": "主贡献证据",
        "evidence": evidence,
        "matched_directions": ["memory"],
    }


def test_screening_uses_ids_and_full_abstract_not_model_array_order():
    tail = "Our agent retrieves episodic memory for task planning."
    papers = [
        {"title": "Relevant study", "abstract": "Background. " * 80 + tail},
        {"title": "Unrelated study", "abstract": "We predict stock market prices."},
    ]
    invoke = AsyncMock(
        return_value=response(
            [
                decision("p1", "We predict stock market prices.", 1, "excluded"),
                decision("p0", tail),
            ]
        )
    )
    audit = []
    result = asyncio.run(
        deep.filter_papers_with_reason(papers, "robot memory", invoke, [], 1, audit=audit)
    )
    assert [p["title"] for p in result] == ["Relevant study"]
    assert tail in invoke.call_args.args[0][1].content
    assert [p["screening_status"] for p in audit] == ["included", "excluded"]
    assert result[0]["relevance_evidence"] == tail


def test_retry_only_invalid_ids_then_pending_without_fabricated_score():
    papers = [
        {"title": "A", "abstract": "Robot memory improves task planning."},
        {"title": "B", "abstract": "Robot perception detects obstacles."},
    ]
    invoke = AsyncMock(
        side_effect=[
            response(
                [
                    decision("p0", "Robot memory improves task planning."),
                    decision("p1", "invented evidence nowhere in abstract"),
                ]
            ),
            response([decision("p1", "still fabricated evidence")]),
        ]
    )
    audit = []
    result = asyncio.run(
        deep.filter_papers_with_reason(papers, "robot memory", invoke, [], 1, audit=audit)
    )
    assert len(result) == 1
    assert audit[1]["screening_status"] == "pending"
    assert "relevance_score" not in audit[1]
    retry = invoke.call_args_list[1].args[0][1].content
    assert '"p1"' in retry and '"p0"' not in retry
    assert deep.screening_summary(audit)["counts"] == {
        "included": 1,
        "supporting": 0,
        "excluded": 0,
        "pending": 1,
    }


def test_latex_overescaping_does_not_hide_real_contribution_evidence():
    # Regression from a real PAL (2610.29329) screening response: the model
    # doubled already JSON-escaped TeX slashes in otherwise verbatim evidence.
    excerpt = (
        r"At execution time, the VLM selects the currently relevant subtask from the "
        r"\texttt{subtask\_list} and passes it to a low-level vision-language-action (VLA) policy."
    )
    overescaped = excerpt.replace("\\", "\\" * 4)
    papers = [{"title": "PAL", "abstract": excerpt}]
    invoke = AsyncMock(return_value=response([decision("p0", overescaped, 5, "core")]))
    result = asyncio.run(deep.filter_papers_with_reason(papers, "VLA memory", invoke, [], 1))
    assert len(result) == 1
    assert result[0]["relevance_score"] == 5
    invoke.assert_awaited_once()

    # Normalizing serialization artifacts must not permit changed claims.
    invented = overescaped.replace("selects", "discards")
    invoke = AsyncMock(return_value=response([decision("p0", invented, 5, "core")]))
    audit = []
    result = asyncio.run(
        deep.filter_papers_with_reason(papers, "VLA memory", invoke, [], 1, audit=audit)
    )
    assert result == []
    assert audit[0]["screening_status"] == "pending"
    assert invoke.await_count == 2


def test_missing_abstract_preserved_as_pending_without_model_call():
    invoke = AsyncMock(side_effect=RuntimeError("offline"))
    audit = []
    result = asyncio.run(
        deep.filter_papers_with_reason([{"title": "Unknown"}], "robot", invoke, [], 1, audit=audit)
    )
    assert result == [] and len(audit) == 1
    assert audit[0]["screening_status"] == "pending"
    assert "relevance_score" not in audit[0]
    invoke.assert_not_awaited()


def test_withdrawn_metadata_excluded_without_model_call():
    invoke = AsyncMock(side_effect=AssertionError("withdrawal already known"))
    audit = []
    result = asyncio.run(
        deep.filter_papers_with_reason(
            [
                {
                    "title": "Robot memory",
                    "abstract": "This paper has been withdrawn by the authors.",
                }
            ],
            "robot memory",
            invoke,
            [],
            1,
            audit=audit,
        )
    )
    assert result == []
    assert audit[0]["screening_status"] == "excluded"
    assert "撤稿" in audit[0]["relevance_reason"]
    invoke.assert_not_awaited()


def test_quick_mode_keeps_every_screened_paper_and_publishes_audit(monkeypatch):
    papers = [{"title": f"Robot {i}", "abstract": "Robot memory plans tasks."} for i in range(3)]
    monkeypatch.setattr(
        deep, "understand_topic", AsyncMock(return_value={"seed_queries": ["robot memory"]})
    )
    monkeypatch.setattr(deep, "_run_search", AsyncMock(return_value=papers))
    invoke = AsyncMock(
        return_value=response([decision(f"p{i}", "Robot memory plans tasks.") for i in range(3)])
    )
    progress = Mock()
    result = asyncio.run(
        deep.run_deep_search(
            topic="robot memory",
            year_from=2021,
            max_rounds=1,
            quick_mode=True,
            llm_invoke=invoke,
            set_step=Mock(),
            is_cancelled=lambda: False,
            log=[],
            on_progress=progress,
        )
    )
    assert len(result["papers"]) == 3
    assert len(progress.call_args.args[0]["papers"]) == 3
    assert result["screening"]["counts"]["included"] == 3
    assert result["coverage_status"] == "budget_exhausted"


def test_coverage_cannot_claim_sufficient_without_real_direction_evidence():
    invoke = AsyncMock(
        return_value=response(
            {
                "coverage_sufficient": True,
                "direction_evidence": {"memory": ["Invented A", "Invented B"]},
                "missing_directions": [],
            }
        )
    )
    result = asyncio.run(
        deep.review_coverage(
            "robot memory",
            {"sub_directions": ["memory"]},
            [{"title": "Real title", "relevance_score": 5}],
            ["robot memory"],
            invoke,
        )
    )
    assert result["coverage_sufficient"] is False


def test_bad_topic_plan_retried_without_chinese_search_fallback():
    invoke = AsyncMock(
        side_effect=[
            response({"seed_queries": "invalid"}),
            response({"seed_queries": ["robot memory", "Robot Memory", None]}),
        ]
    )
    result = asyncio.run(deep.understand_topic("机器人记忆", invoke, []))
    assert result["seed_queries"] == ["robot memory"]
    assert invoke.await_count == 2


def test_retracted_source_flag_excludes_even_without_abstract():
    invoke = AsyncMock()
    audit = []
    result = asyncio.run(
        deep.filter_papers_with_reason(
            [{"title": "Retracted paper", "is_retracted": True}],
            "robot",
            invoke,
            [],
            1,
            audit=audit,
        )
    )
    assert result == []
    assert audit[0]["screening_status"] == "excluded"
    invoke.assert_not_awaited()


def test_pending_reevaluated_only_when_new_source_supplies_more_evidence(monkeypatch):
    weak = {"title": "Same paper", "abstract": ""}
    complete = {"title": "Same paper", "abstract": "Robot memory improves planning."}
    monkeypatch.setattr(
        deep,
        "understand_topic",
        AsyncMock(return_value={"seed_queries": [f"q{i}" for i in range(16)]}),
    )
    monkeypatch.setattr(deep, "_run_search", AsyncMock(side_effect=[[weak], [complete]]))
    invoke = AsyncMock(
        side_effect=[
            response([decision("p0", complete["abstract"])]),
        ]
    )
    result = asyncio.run(
        deep.run_deep_search(
            topic="robot memory",
            year_from=2021,
            max_rounds=2,
            quick_mode=True,
            llm_invoke=invoke,
            set_step=Mock(),
            is_cancelled=lambda: False,
            log=[],
        )
    )
    assert len(result["papers"]) == 1
    assert result["screening"]["counts"] == {
        "included": 1,
        "supporting": 0,
        "excluded": 0,
        "pending": 0,
    }
    assert result["papers"][0]["abstract"] == complete["abstract"]


def test_coverage_includes_late_papers_and_rejects_cross_direction_evidence():
    papers = [
        {
            "title": f"Paper {i}",
            "relevance_score": 5,
            "matched_directions": ["memory"],
            "relevance_evidence": "Robot memory contribution.",
        }
        for i in range(30)
    ]
    invoke = AsyncMock(
        return_value=response(
            {
                "coverage_sufficient": True,
                "direction_evidence": {
                    "memory": ["Paper 28", "Paper 29"],
                    "planning": ["Paper 28", "Paper 29"],
                },
                "missing_directions": [],
            }
        )
    )
    result = asyncio.run(
        deep.review_coverage(
            "robot memory or planning",
            {"sub_directions": ["memory", "planning"]},
            papers,
            [],
            invoke,
        )
    )
    assert "Paper 29" in invoke.call_args.args[0][1].content
    assert result["coverage_sufficient"] is False


def test_source_failure_is_not_reported_as_evidence_of_no_papers(monkeypatch):
    from backend.retrieval import SearchResults

    failed = SearchResults()
    failed.diagnostics = [{"source": "arxiv", "status": "error"}]
    failed.warnings = ["arxiv source unavailable"]
    monkeypatch.setattr(
        deep, "understand_topic", AsyncMock(return_value={"seed_queries": ["robot memory"]})
    )
    monkeypatch.setattr(deep, "_run_search", AsyncMock(return_value=failed))
    invoke = AsyncMock()
    result = asyncio.run(
        deep.run_deep_search(
            topic="robot memory",
            year_from=2021,
            max_rounds=1,
            quick_mode=True,
            llm_invoke=invoke,
            set_step=Mock(),
            is_cancelled=lambda: False,
            log=[],
        )
    )
    assert result["coverage_status"] == "partial_sources"
    assert "无法确认" in result["empty_reason"]
    assert result["iteration_log"][0]["search_diagnostics"][0]["status"] == "error"
    invoke.assert_not_awaited()


def test_transferable_papers_preserved_as_supporting_not_primary_or_summary(monkeypatch):
    papers = [
        {"title": "Direct robot planning", "abstract": "Robot planning with episodic memory."},
        {
            "title": "Transfer from driving",
            "abstract": "Autonomous driving uses world model memory.",
        },
    ]
    monkeypatch.setattr(
        deep, "understand_topic", AsyncMock(return_value={"seed_queries": ["robot memory"]})
    )
    monkeypatch.setattr(deep, "_run_search", AsyncMock(return_value=papers))
    invoke = AsyncMock(
        return_value=response(
            [
                decision("p0", papers[0]["abstract"], 4, "subtopic"),
                decision("p1", papers[1]["abstract"], 3, "transferable"),
            ]
        )
    )
    summary = AsyncMock(return_value="Only direct evidence")
    monkeypatch.setattr(deep, "generate_deep_summary", summary)
    progress = Mock()
    result = asyncio.run(
        deep.run_deep_search(
            topic="robot memory",
            year_from=2021,
            max_rounds=1,
            quick_mode=False,
            llm_invoke=invoke,
            set_step=Mock(),
            is_cancelled=lambda: False,
            log=[],
            on_progress=progress,
        )
    )
    assert [p["title"] for p in result["papers"]] == ["Direct robot planning"]
    assert result["screening"]["supporting"][0]["title"] == "Transfer from driving"
    assert result["screening"]["supporting"][0]["relevance_score"] == 3
    assert progress.call_args.args[0]["screening"]["counts"]["supporting"] == 1
    assert [p["title"] for p in summary.call_args.args[1]] == ["Direct robot planning"]


def test_model_failure_with_abstract_retries_then_preserves_pending():
    invoke = AsyncMock(side_effect=RuntimeError("offline"))
    audit = []
    papers = [{"title": "Unknown", "abstract": "Robot memory improves planning."}]
    result = asyncio.run(
        deep.filter_papers_with_reason(papers, "robot", invoke, [], 1, audit=audit)
    )
    assert result == []
    assert audit[0]["screening_status"] == "pending"
    assert "relevance_score" not in audit[0]
    assert invoke.await_count == 2


def test_task_decomposition_variants_preserve_domain_and_scope():
    queries = ["robot hierarchical planning", "world model subtask planning", "robot memory"]
    expanded = deep.expand_planning_queries("hierarchical robot subtask planning", queries)
    assert expanded == [
        "robot hierarchical planning",
        'robot "task decomposition"',
        'robot "hierarchical"',
        "world model subtask planning",
        'world model "task decomposition"',
        'world model "hierarchical"',
        "robot memory",
    ]
    assert deep.expand_planning_queries("robot path planning", queries) == queries
    assert deep.expand_planning_queries("robot memory", queries) == queries
    assert deep.expand_planning_queries("子任务规划", expanded) == expanded


def test_full_search_screens_one_reference_hop_and_keeps_audit(monkeypatch):
    seed = {
        "title": "Seed",
        "abstract": "Robot task planning.",
        "relevance_score": 5,
        "referenced_works": ["https://openalex.org/W123"],
    }
    cited = {"title": "Cited study", "abstract": "Robot memory supports planning."}
    monkeypatch.setattr(
        deep, "understand_topic", AsyncMock(return_value={"seed_queries": ["robot planning"]})
    )
    monkeypatch.setattr(deep, "_run_search", AsyncMock(return_value=[seed]))
    citations = AsyncMock(return_value=[cited])
    monkeypatch.setattr(deep, "_run_reference_search", citations)
    monkeypatch.setattr(
        deep, "generate_deep_summary", AsyncMock(return_value="Evidence-based summary")
    )
    invoke = AsyncMock(
        side_effect=[
            response([decision("p0", seed["abstract"], 5, "core")]),
            response([decision("p0", cited["abstract"], 4, "subtopic")]),
        ]
    )
    result = asyncio.run(
        deep.run_deep_search(
            topic="robot planning",
            year_from=2021,
            max_rounds=1,
            quick_mode=False,
            llm_invoke=invoke,
            set_step=Mock(),
            is_cancelled=lambda: False,
            log=[],
        )
    )
    assert {p["title"] for p in result["papers"]} == {"Seed", "Cited study"}
    assert result["iteration_log"][-1]["kind"] == "references"
    assert result["iteration_log"][-1]["new_retained"] == 1
    assert result["screening"]["counts"]["included"] == 2
    citations.assert_awaited_once()


def test_quick_search_does_not_expand_references(monkeypatch):
    seed = {
        "title": "Seed",
        "abstract": "Robot task planning.",
        "relevance_score": 5,
        "referenced_works": ["https://openalex.org/W123"],
    }
    monkeypatch.setattr(
        deep, "understand_topic", AsyncMock(return_value={"seed_queries": ["robot planning"]})
    )
    monkeypatch.setattr(deep, "_run_search", AsyncMock(return_value=[seed]))
    citations = AsyncMock(side_effect=AssertionError("Quick mode must stay bounded"))
    monkeypatch.setattr(deep, "_run_reference_search", citations)
    invoke = AsyncMock(return_value=response([decision("p0", seed["abstract"], 5, "core")]))
    result = asyncio.run(
        deep.run_deep_search(
            topic="robot planning",
            year_from=2021,
            max_rounds=1,
            quick_mode=True,
            llm_invoke=invoke,
            set_step=Mock(),
            is_cancelled=lambda: False,
            log=[],
        )
    )
    assert len(result["papers"]) == 1
    citations.assert_not_awaited()


@pytest.mark.parametrize("citation_phase", [False, True])
def test_cancel_preserves_completed_batch_in_persisted_task(monkeypatch, citation_phase):
    """The real cancel endpoint path retains already screened evidence on disk."""
    from backend import executor
    from backend.task_manager import task_manager
    from backend.task_paths import task_metadata_file

    seed = {
        "title": "Seed",
        "abstract": "Robot memory plans tasks.",
        "referenced_works": ["https://openalex.org/W123"],
    }
    candidates = [
        {"title": f"Candidate {i}", "abstract": "Robot memory plans tasks."} for i in range(9)
    ]
    monkeypatch.setattr(
        deep, "understand_topic", AsyncMock(return_value={"seed_queries": ["robot memory"]})
    )
    monkeypatch.setattr(
        deep, "_run_search", AsyncMock(return_value=[seed] if citation_phase else candidates)
    )
    monkeypatch.setattr(deep, "_run_reference_search", AsyncMock(return_value=candidates))
    monkeypatch.setattr(executor, "get_llm", Mock())
    monkeypatch.setattr(executor, "save_history", Mock())
    task = task_manager.create_task(
        keywords=["robot memory"], topic="robot memory", search_mode="deep", deep_rounds=1
    )

    async def exercise():
        persisted_batch = asyncio.Event()
        original_update = task_manager.update_task

        def update(task_id, **kwargs):
            result = original_update(task_id, **kwargs)
            rounds = (kwargs.get("results") or {}).get("iteration_log") or []
            if rounds and rounds[-1].get("screened_candidates") == 8:
                persisted_batch.set()
            return result

        monkeypatch.setattr(task_manager, "update_task", update)

        async def invoke(model, messages, **kwargs):
            content = messages[1].content
            if '"p8"' in content:
                await asyncio.Event().wait()
            if '"Seed\\n' in content:
                return response([decision("p0", seed["abstract"], 5, "core")])
            values = [
                decision(
                    f"p{i}",
                    candidates[i]["abstract"],
                    5 if i < 3 else 3 if i < 5 else 1,
                    "core" if i < 3 else "transferable" if i < 5 else "excluded",
                )
                for i in range(7)
            ]
            values.append({"id": "p7", "decision": "pending", "reason": "Needs full text"})
            return response(values)

        monkeypatch.setattr(executor, "_llm_invoke", invoke)
        task_manager.start_task(task.id, executor.execute_task(task.id))
        future = task_manager._running[task.id]
        try:
            await asyncio.wait_for(persisted_batch.wait(), timeout=2)
            assert task_manager.cancel_task(task.id)
        finally:
            if not future.done():
                future.cancel()
            await asyncio.gather(future, return_exceptions=True)
        persisted = json.loads(task_metadata_file(task.id).read_text())
        assert persisted["status"] == "cancelled"
        screening = persisted["results"]["screening"]
        assert screening["counts"] == {
            "included": 3 + int(citation_phase),
            "supporting": 2,
            "excluded": 2,
            "pending": 1,
        }
        assert len(persisted["papers"]) == screening["counts"]["included"]
        assert {p["title"] for p in persisted["papers"]} == {
            p["title"] for p in screening["included"]
        }
        current = persisted["results"]["iteration_log"][-1]
        assert current["status"] == "screening"
        assert current["screened_candidates"] == 8
        assert current["new_retained"] == 3
        assert current["new_candidates"] == 9
        assert (current.get("kind") == "references") is citation_phase

    asyncio.run(exercise())


def test_completed_batches_are_not_counted_twice(monkeypatch):
    papers = [
        {"title": f"Candidate {i}", "abstract": "Robot memory plans tasks."} for i in range(9)
    ]
    monkeypatch.setattr(
        deep, "understand_topic", AsyncMock(return_value={"seed_queries": ["robot memory"]})
    )
    monkeypatch.setattr(deep, "_run_search", AsyncMock(return_value=papers))
    snapshots = []

    async def invoke(messages):
        ids = ["p8"] if '"p8"' in messages[1].content else [f"p{i}" for i in range(8)]
        return response([decision(pid, "Robot memory plans tasks.", 5, "core") for pid in ids])

    def snapshot(value):
        snapshots.append(json.loads(json.dumps(value)))

    result = asyncio.run(
        deep.run_deep_search(
            topic="robot memory",
            year_from=2021,
            max_rounds=1,
            quick_mode=True,
            llm_invoke=invoke,
            set_step=Mock(),
            is_cancelled=lambda: False,
            log=[],
            on_progress=snapshot,
        )
    )
    assert len(result["papers"]) == 9
    assert result["screening"]["counts"]["included"] == 9
    assert result["iteration_log"][0]["new_retained"] == 9
    assert result["iteration_log"][0]["screened_candidates"] == 9
    assert len(result["iteration_log"]) == 1
    assert any(s["iteration_log"][0]["screened_candidates"] < 9 for s in snapshots)
    for s in snapshots:
        assert len(s["papers"]) == s["screening"]["counts"]["included"]
        assert len({p["title"] for p in s["screening"]["included"]}) == len(s["papers"])


def test_full_search_records_missing_reference_metadata_without_network(monkeypatch):
    from backend import retrieval

    paper = {"title": "Seed without citation metadata", "abstract": "Robot task planning."}
    monkeypatch.setattr(
        deep, "understand_topic", AsyncMock(return_value={"seed_queries": ["robot planning"]})
    )
    monkeypatch.setattr(deep, "_run_search", AsyncMock(return_value=[paper]))
    network = Mock(side_effect=AssertionError("No reference IDs means no API request"))
    monkeypatch.setattr(retrieval, "_get", network)
    monkeypatch.setattr(deep, "generate_deep_summary", AsyncMock(return_value="Supported summary"))
    invoke = AsyncMock(return_value=response([decision("p0", paper["abstract"], 5, "core")]))
    progress = Mock()
    result = asyncio.run(
        deep.run_deep_search(
            topic="robot planning",
            year_from=2021,
            max_rounds=1,
            quick_mode=False,
            llm_invoke=invoke,
            set_step=Mock(),
            is_cancelled=lambda: False,
            log=[],
            on_progress=progress,
        )
    )

    assert len(result["papers"]) == 1
    assert result["summary"] == "Supported summary"
    reference_round = result["iteration_log"][-1]
    assert reference_round["kind"] == "references"
    assert reference_round["found"] == 0
    assert reference_round["new_retained"] == 0
    assert reference_round["search_diagnostics"][0]["status"] == "no_reference_metadata"
    assert reference_round["warnings"]
    assert result["coverage_status"] == "budget_exhausted"
    # The executor persists this progress payload; diagnostics exist before the summary call.
    assert progress.call_args.args[0]["iteration_log"][-1] == reference_round
    assert progress.call_args.args[0]["coverage_status"] == "budget_exhausted"
    network.assert_not_called()
    invoke.assert_awaited_once()

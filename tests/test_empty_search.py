"""Empty searches must not produce unsupported model-generated literature reviews."""

import asyncio
import importlib
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage

from backend import deep_search_agent as deep
from backend import executor
from backend.schemas import PaperInfo
from backend.task_manager import task_manager


@pytest.mark.parametrize("mode", ["keyword", "deep"])
@pytest.mark.parametrize("quick", [False, True])
def test_empty_workflows_skip_filter_and_review_but_save_history(monkeypatch, mode, quick):
    task = task_manager.create_task(
        keywords=["empty"],
        search_mode=mode,
        topic="empty" if mode == "deep" else None,
        quick_mode=quick,
    )
    model = Mock()
    monkeypatch.setattr(executor, "get_llm", model)
    monkeypatch.setattr(executor, "_parallel_search", AsyncMock(return_value=[]))
    monkeypatch.setattr(executor, "search_papers", Mock(return_value=[]))
    plan = {"seed_queries": [f"query {i}" for i in range(9)]}
    monkeypatch.setattr(deep, "understand_topic", AsyncMock(return_value=plan))
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(deep, "_run_search", search)
    forbidden = []
    for module, name in [
        (executor, "_parallel_relevance_filter"),
        (executor, "_llm_invoke_with_progress"),
        (executor, "_parallel_translate"),
        (deep, "filter_papers_with_reason"),
        (deep, "review_coverage"),
        (deep, "generate_deep_summary"),
    ]:
        mock = AsyncMock(side_effect=AssertionError(f"Unexpected {name} for empty papers"))
        monkeypatch.setattr(module, name, mock)
        forbidden.append(mock)
    save = Mock()
    monkeypatch.setattr(executor, "save_history", save)

    asyncio.run(executor.execute_task(task.id))

    assert task.status == "completed", task.error
    assert task.papers == []
    assert [step.status for step in task.steps] == ["done", "done", "skipped", "skipped", "done"]
    assert "未检索到论文" in task.steps[3].detail
    assert not task.results.get("literature_review")
    assert not task.results.get("deep_research_summary")
    for mock in forbidden:
        mock.assert_not_awaited()
    save.assert_called_once()
    assert save.call_args.kwargs["literature_review"] == ""
    assert "跳过" in save.call_args.kwargs["execution_log"]
    if mode == "keyword":
        model.assert_not_called()
    else:
        assert search.await_count == 2, (
            "Still search remaining planned queries without coverage LLM"
        )
        assert len(task.results["iteration_log"]) == 2
        assert task.results["topic_plan"] == plan


@pytest.mark.parametrize("mode", ["keyword", "deep"])
def test_all_rejected_papers_skip_review(monkeypatch, mode):
    task = task_manager.create_task(
        keywords=["robot"], search_mode=mode, topic="robot", deep_rounds=1
    )
    paper = {"title": "Unrelated paper", "abstract": "Not about robots"}
    monkeypatch.setattr(executor, "get_llm", Mock())
    if mode == "keyword":
        monkeypatch.setattr(executor, "_parallel_search", AsyncMock(return_value=[paper]))
        # Exercise the actual keyword filter: valid low scores must not be restored.
        monkeypatch.setattr(
            executor,
            "_llm_invoke",
            AsyncMock(
                return_value=AIMessage(
                    content='[{"id":"p0","score":1,"decision":"exclude","match_scope":"excluded","reason":"Unrelated","evidence":"Not about robots"}]'
                )
            ),
        )
    else:
        monkeypatch.setattr(
            deep, "understand_topic", AsyncMock(return_value={"seed_queries": ["robot"]})
        )
        monkeypatch.setattr(deep, "_run_search", AsyncMock(return_value=[paper]))
        monkeypatch.setattr(deep, "filter_papers_with_reason", AsyncMock(return_value=[]))
    summary = AsyncMock()
    review = AsyncMock()
    translate = AsyncMock()
    monkeypatch.setattr(deep, "generate_deep_summary", summary)
    monkeypatch.setattr(executor, "_llm_invoke_with_progress", review)
    monkeypatch.setattr(executor, "_parallel_translate", translate)
    monkeypatch.setattr(executor, "save_history", Mock())
    asyncio.run(executor.execute_task(task.id))
    assert task.status == "completed", task.error
    assert task.papers == []
    assert task.steps[2].status == "done"
    assert task.steps[3].status == "skipped"
    assert "过滤后无可用论文" in task.steps[3].detail
    summary.assert_not_awaited()
    review.assert_not_awaited()
    translate.assert_not_awaited()


def test_failed_scoring_does_not_invent_positive_scores(monkeypatch):
    monkeypatch.setattr(executor, "_llm_invoke", AsyncMock(side_effect=RuntimeError("offline")))
    papers = [{"title": "Paper"}]
    result = asyncio.run(executor._parallel_relevance_filter(papers, "topic", Mock(), []))
    assert result == []
    assert "relevance_score" not in papers[0]


@pytest.mark.parametrize("empty_first", [True, False])
def test_deep_search_summarizes_retained_papers_even_if_one_round_is_empty(
    monkeypatch, empty_first
):
    paper = {"title": "Robot paper", "abstract": "Study", "relevance_score": 5}
    monkeypatch.setattr(
        deep,
        "understand_topic",
        AsyncMock(return_value={"seed_queries": [f"query {i}" for i in range(9)]}),
    )
    monkeypatch.setattr(
        deep, "_run_search", AsyncMock(side_effect=[[], [paper]] if empty_first else [[paper], []])
    )
    filtering = AsyncMock(return_value=[paper])
    coverage = AsyncMock(
        return_value={"coverage_sufficient": False, "next_queries": ["another query"]}
    )
    summary = AsyncMock(return_value="Supported summary")
    monkeypatch.setattr(deep, "filter_papers_with_reason", filtering)
    monkeypatch.setattr(deep, "review_coverage", coverage)
    monkeypatch.setattr(deep, "generate_deep_summary", summary)
    steps = Mock()
    result = asyncio.run(
        deep.run_deep_search(
            topic="robot",
            year_from=2025,
            max_rounds=2,
            quick_mode=False,
            llm_invoke=AsyncMock(),
            set_step=steps,
            is_cancelled=lambda: False,
            log=[],
        )
    )
    assert len(result["papers"]) == 1
    assert result["summary"] == "Supported summary"
    filtering.assert_awaited_once()
    assert coverage.await_count == (0 if empty_first else 1)
    summary.assert_awaited_once()
    assert steps.call_args.args[1] == "done"


def test_cli_agent_skips_review_after_empty_fallback(monkeypatch):
    agent = importlib.import_module("agents.literature_agent")
    monkeypatch.setattr(agent, "expand_search_queries", lambda keywords, log: keywords)
    monkeypatch.setattr(agent, "search_papers_multi_query", Mock(return_value=[]))
    monkeypatch.setattr("tools.search_papers_per_query", Mock(return_value=[]))
    model = Mock(side_effect=AssertionError("No review model needed"))
    monkeypatch.setattr(agent, "get_llm", model)
    result = agent.literature_agent({"keywords": ["empty"], "year_from": 2025})
    assert result["papers"] == [] and result["literature_review"] == ""
    assert "跳过" in result["execution_log"][-1]
    model.assert_not_called()


def test_keyword_fallback_with_results_still_generates_review(monkeypatch):
    task = task_manager.create_task(keywords=["robot"])
    paper = {"title": "Relevant robot paper", "abstract": "Study", "relevance_score": 5}
    monkeypatch.setattr(executor, "get_llm", Mock())
    monkeypatch.setattr(executor, "_parallel_search", AsyncMock(return_value=[]))
    monkeypatch.setattr(executor, "search_papers", Mock(return_value=[paper]))
    monkeypatch.setattr(
        executor,
        "_llm_invoke",
        AsyncMock(
            return_value=AIMessage(
                content='[{"id":"p0","score":5,"decision":"include","match_scope":"core","reason":"Relevant","evidence":"Relevant robot paper"}]'
            )
        ),
    )
    review = AsyncMock(return_value=AIMessage(content="Review grounded in the paper"))
    monkeypatch.setattr(executor, "_llm_invoke_with_progress", review)
    monkeypatch.setattr(executor, "_parallel_translate", AsyncMock(return_value=[paper]))
    monkeypatch.setattr(executor, "save_history", Mock())
    asyncio.run(executor.execute_task(task.id))
    assert task.status == "completed", task.error
    assert all(step.status == "done" for step in task.steps)
    assert len(task.papers) == 1
    assert isinstance(task.papers[0], PaperInfo)
    assert task.results["literature_review"] == "Review grounded in the paper"
    review.assert_awaited_once()

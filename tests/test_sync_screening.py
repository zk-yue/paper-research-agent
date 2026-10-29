"""CLI and web share evidence-based screening; no real model/network calls."""

import asyncio
import importlib
import json
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage

from tools import filter_papers_by_relevance


def verdict(pid, quote, score, scope):
    return {
        "id": pid,
        "decision": "include" if score >= 3 else "exclude",
        "score": score,
        "match_scope": scope,
        "reason": "Evidence of main contribution",
        "evidence": quote,
        "matched_directions": ["memory"],
    }


def test_sync_filter_keeps_full_abstract_and_separates_supporting_reading():
    evidence = "Our robot retrieves task memory for planning."
    papers = [
        {"title": "Memory robot", "abstract": "Background. " * 80 + evidence},
        {"title": "Transfer method", "abstract": "Our transferable method can support memory."},
        {"title": "Unrelated hardware", "abstract": "Our hardware improves cache allocation."},
    ]
    llm = Mock()
    llm.invoke.return_value = AIMessage(
        content=json.dumps(
            [
                verdict("p2", papers[2]["abstract"], 1, "excluded"),
                verdict("p0", evidence, 5, "core"),
                verdict("p1", papers[1]["abstract"], 3, "transferable"),
            ]
        )
    )
    result = filter_papers_by_relevance(papers, "robot memory or planning", llm, [])
    assert [p["title"] for p in result] == ["Memory robot"]
    assert evidence in llm.invoke.call_args.args[0][1].content
    assert result.screening["counts"] == {
        "included": 1,
        "supporting": 1,
        "excluded": 1,
        "pending": 0,
    }
    assert result.screening["supporting"][0]["title"] == "Transfer method"
    assert "relevance_score" not in papers[0]


def test_sync_filter_failure_retains_pending_audit_without_inventing_score():
    papers = [{"title": "A robot", "abstract": "We study robotic memory."}]
    llm = Mock()
    llm.invoke.side_effect = RuntimeError("offline")
    result = filter_papers_by_relevance(papers, "robot memory", llm, [])
    assert result == []
    assert result.screening["counts"]["pending"] == 1
    assert "relevance_score" not in result.screening["pending"][0]
    assert llm.invoke.call_count == 2


def test_sync_filter_does_not_nest_an_event_loop():
    async def caller():
        with pytest.raises(RuntimeError, match="Async callers"):
            filter_papers_by_relevance([{"title": "test"}], "topic", Mock(), [])

    asyncio.run(caller())


def test_cli_agent_does_not_summarize_unverified_candidates(monkeypatch):
    agent = importlib.import_module("agents.literature_agent")
    monkeypatch.setattr(agent, "expand_search_queries", lambda keywords, log: keywords)
    monkeypatch.setattr(
        agent,
        "search_papers_multi_query",
        Mock(
            return_value=[{"title": "Possible candidate", "abstract": "A robot uses task memory."}]
        ),
    )
    llm = Mock()
    llm.invoke.side_effect = RuntimeError("model unavailable")
    monkeypatch.setattr(agent, "get_llm", Mock(return_value=llm))
    translate = Mock(side_effect=AssertionError("No translation of unverified evidence"))
    monkeypatch.setattr(agent, "translate_abstracts", translate)
    result = agent.literature_agent({"keywords": ["robot memory"], "year_from": 2021})
    assert result["literature_review"] == ""
    assert result["papers"] == []
    assert result["screening"]["counts"]["pending"] == 1
    assert "暂无已确认直接相关论文" in result["execution_log"][-1]
    translate.assert_not_called()


def test_history_preserves_cli_audit_and_retrieval_diagnostics():
    from history import load_history_detail, save_history

    details = {
        "screening": {"pending": [{"title": "Candidate"}]},
        "search_diagnostics": [{"source": "arxiv", "status": "error"}],
    }
    history_id = save_history(["robot memory"], 2021, "", "", "pending", research_details=details)
    assert load_history_detail(history_id)["research_details"] == details

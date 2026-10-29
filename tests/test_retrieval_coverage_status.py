import asyncio
import json
from unittest.mock import AsyncMock, Mock

from langchain_core.messages import AIMessage

from backend import deep_search_agent as deep
from backend.retrieval import SearchResults


def test_successfully_capped_results_are_not_described_as_unavailable_sources(monkeypatch):
    results = SearchResults(
        [{"title": "Robot memory", "abstract": "Our robot uses episodic memory."}],
        diagnostics=[
            {"source": "arxiv", "status": "success", "count": 1, "total": 100, "truncated": True}
        ],
        warnings=["部分查询达到返回上限"],
    )
    monkeypatch.setattr(
        deep, "understand_topic", AsyncMock(return_value={"seed_queries": ["robot memory"]})
    )
    monkeypatch.setattr(deep, "_run_search", AsyncMock(return_value=results))
    invoke = AsyncMock(
        return_value=AIMessage(
            content=json.dumps(
                [
                    {
                        "id": "p0",
                        "decision": "include",
                        "score": 4,
                        "match_scope": "core",
                        "reason": "Task memory method",
                        "evidence": "Our robot uses episodic memory.",
                        "matched_directions": ["memory"],
                    }
                ]
            )
        )
    )
    step = Mock()
    result = asyncio.run(
        deep.run_deep_search(
            topic="robot memory",
            year_from=2021,
            max_rounds=1,
            quick_mode=True,
            llm_invoke=invoke,
            set_step=step,
            is_cancelled=lambda: False,
            log=[],
        )
    )
    assert result["coverage_status"] == "bounded_results"
    assert len(result["papers"]) == 1
    done_search = [call.args[2] for call in step.call_args_list if call.args[:2] == (1, "done")]
    assert len(done_search) == 1 and "结果上限" in done_search[0]
    assert "不可用" not in done_search[0]

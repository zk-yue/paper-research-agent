import json
from unittest.mock import Mock

import pytest

from backend import retrieval
from tools import merge_paper_lists, search_papers_per_query


def test_query_filters_publication_window_before_result_limit():
    exact = retrieval.arxiv_expression('"vision-language-action" "task decomposition"', 2022)
    relaxed = retrieval.arxiv_expression(
        '"vision-language-action" "task decomposition"', 2022, relaxed=True
    )
    assert '(ti:"task decomposition" OR abs:"task decomposition")' in exact
    assert "submittedDate:[202201010000 TO 300001010000]" in exact
    assert "(ti:decomposition OR abs:decomposition)" in relaxed
    assert "(ti:vision OR abs:vision)" in relaxed
    assert "all:" not in relaxed


def test_actual_empty_and_unavailable_are_not_conflated(monkeypatch):
    def arxiv(query, *_args):
        return [], 0, query

    def openalex(*_args):
        raise retrieval.SourceError("HTTP 403：检索源拒绝访问", blocked=True)

    monkeypatch.setattr(retrieval, "_arxiv", arxiv)
    monkeypatch.setattr(retrieval, "_openalex", openalex)
    result = retrieval.retrieve_queries(["robot memory", "robot planning"], limit=10)
    assert result == []
    assert [(d["source"], d["status"]) for d in result.diagnostics] == [
        ("arxiv", "empty"),
        ("arxiv", "empty"),
        ("openalex", "error"),
        ("openalex", "skipped"),
    ]
    assert any("403" in warning for warning in result.warnings)


def test_sparse_phrase_query_gets_relaxed_pass_and_provenance(monkeypatch):
    calls = []

    def fetch(query, limit, year, relaxed, deadline):
        calls.append((query, limit, year, relaxed))
        papers = (
            [
                {
                    "title": "Memory robot",
                    "paper_id": "2601.12345",
                    "source": "arxiv",
                    "matched_queries": [query],
                }
            ]
            if relaxed
            else []
        )
        return papers, 500 if relaxed else 0, "effective query"

    monkeypatch.setattr(retrieval, "_arxiv", fetch)
    result = retrieval.retrieve_queries(['"robot planning" memory'], 10, 2021, sources=["arxiv"])
    assert [c[3] for c in calls] == [False, True]
    assert result[0]["retrieval_provenance"][0]["variant"] == "relaxed"
    assert result.diagnostics[1]["truncated"]
    assert any("上限" in warning for warning in result.warnings)


def test_wrappers_preserve_diagnostics_and_merge_all_query_provenance(monkeypatch):
    papers = [
        {
            "paper_id": "2601.12345v1",
            "title": "Memory robot",
            "source": "arxiv",
            "matched_queries": ["robot memory"],
            "retrieval_provenance": [{"source": "arxiv", "query": "robot memory"}],
        },
        {
            "paper_id": "W123",
            "url": "https://arxiv.org/abs/2601.12345",
            "title": "Memory robot",
            "source": "openalex",
            "matched_queries": ["robot planning"],
            "retrieval_provenance": [{"source": "openalex", "query": "robot planning"}],
        },
    ]
    result = retrieval.SearchResults(
        papers, diagnostics=[{"status": "success"}], warnings=["capped"]
    )
    monkeypatch.setattr("tools.retrieve_queries", Mock(return_value=result))
    merged = search_papers_per_query(["robot memory"])
    assert len(merged) == 1
    assert merged.diagnostics == [{"status": "success"}]
    assert merged.warnings == ["capped"]
    assert merged[0]["source"] == "arxiv+openalex"
    assert merged[0]["matched_queries"] == ["robot memory", "robot planning"]
    assert len(merged[0]["retrieval_provenance"]) == 2
    assert merge_paper_lists([], merged)[0]["matched_queries"] == merged[0]["matched_queries"]


def test_arxiv_error_entry_does_not_look_like_a_paper(monkeypatch):
    content = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <id>http://arxiv.org/api/errors#incorrect_id_format_for_123</id>
    <summary>invalid query</summary></entry></feed>"""
    monkeypatch.setattr(retrieval, "_get", lambda *args: Mock(content=content))
    with pytest.raises(retrieval.SourceError, match="invalid query"):
        retrieval._arxiv("robot", 5, 2020, False, 0)


def test_openalex_preserves_abstract_and_arxiv_location(monkeypatch):
    data = {
        "meta": {"count": 1},
        "results": [
            {
                "id": "https://openalex.org/W1",
                "title": "Robot memory",
                "abstract_inverted_index": {"Robot": [0], "has": [1], "memory": [2]},
                "locations": [
                    {
                        "landing_page_url": "https://arxiv.org/abs/2601.12345",
                        "pdf_url": "https://arxiv.org/pdf/2601.12345",
                    }
                ],
                "authorships": [{"author": {"display_name": "A Researcher"}}],
                "publication_date": "2026-01-02",
            }
        ],
    }
    fake = Mock()
    fake.json.return_value = json.loads(json.dumps(data))
    monkeypatch.setattr(retrieval, "_get", Mock(return_value=fake))
    papers, total, _ = retrieval._openalex("robot memory", 20, 2020, False, 0)
    assert total == 1
    assert papers[0]["abstract"] == "Robot has memory"
    assert papers[0]["url"] == "https://arxiv.org/abs/2601.12345"
    assert retrieval._get.call_args.args[2]["filter"] == "from_publication_date:2020-01-01"


def test_zero_budget_marks_queries_skipped(monkeypatch):
    fetch = Mock()
    monkeypatch.setattr(retrieval, "_arxiv", fetch)
    result = retrieval.retrieve_queries(["robot memory"], sources=["arxiv"], budget_seconds=0)
    fetch.assert_not_called()
    assert result.diagnostics[0]["status"] == "skipped"
    assert result.warnings


def test_openalex_link_does_not_pretend_arxiv_was_queried():
    papers = [
        {"title": "A memory robot", "source": "openalex", "url": "https://arxiv.org/abs/2601.12345"}
    ]
    assert merge_paper_lists([], papers)[0]["source"] == "openalex"


def test_dedup_keeps_score_and_supporting_verdict_together():
    existing = {
        "title": "A memory robot",
        "source": "openalex",
        "relevance_score": 4,
        "relevance_reason": "direct memory contribution",
        "screening_status": "included",
    }
    incoming = {
        "title": "A memory robot",
        "source": "arxiv",
        "relevance_score": 2,
        "relevance_reason": "background only",
        "screening_status": "excluded",
    }
    merged = merge_paper_lists([existing], [incoming])[0]
    assert merged["relevance_score"] == 4
    assert merged["relevance_reason"] == "direct memory contribution"
    assert merged["screening_status"] == "included"


def test_case_and_whitespace_equivalent_queries_are_not_requested_twice(monkeypatch):
    fetch = Mock(return_value=([], 0, "robot memory"))
    monkeypatch.setattr(retrieval, "_arxiv", fetch)
    retrieval.retrieve_queries(
        ["robot  memory", "Robot memory", " robot memory "], limit=10, sources=["arxiv"]
    )
    assert fetch.call_count == 1


def test_streaming_response_enforces_deadline_and_closes_socket(monkeypatch):
    now = [1.0]
    monkeypatch.setattr(retrieval.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(retrieval._GATES["arxiv"], "wait", Mock())

    def trickle(chunk_size):
        assert chunk_size == 1
        yield b"a"
        now[0] = 11.0
        yield b"b"

    response = Mock(status_code=200)
    response.iter_content.side_effect = trickle
    get = Mock(return_value=response)
    monkeypatch.setattr(retrieval.requests, "get", get)
    with pytest.raises(TimeoutError, match="时间预算"):
        retrieval._get("arxiv", "https://export.arxiv.org/api/query", {}, 10)
    response.close.assert_called_once()
    assert get.call_args.kwargs["stream"] is True
    assert get.call_args.kwargs["timeout"] == (5, 9)


def test_streaming_json_payload_is_read_before_connection_closes(monkeypatch):
    monkeypatch.setattr(retrieval.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(retrieval._GATES["openalex"], "wait", Mock())
    response = Mock(status_code=200)
    response.iter_content.return_value = [b"{", b'"', b"x", b'"', b":", b"1", b"}"]
    monkeypatch.setattr(retrieval.requests, "get", Mock(return_value=response))
    payload = retrieval._get("openalex", "https://api.openalex.org/works", {}, 10)
    assert payload.json() == {"x": 1}
    response.close.assert_called_once()


def test_full_budget_keeps_original_quota_and_adds_bounded_recent_hits(monkeypatch):
    monkeypatch.setattr(retrieval, "datetime", Mock(now=Mock(return_value=Mock(year=2026))))
    calls = []

    def fetch(query, limit, year, relaxed, deadline):
        calls.append((limit, year, relaxed))
        papers = [
            {
                "title": f"Paper {year} {i}",
                "paper_id": f"{year}.{i}",
                "source": "arxiv",
                "published_date": f"{year}-01-01",
            }
            for i in range(limit)
        ]
        return papers, 1000, f"year >= {year}"

    monkeypatch.setattr(retrieval, "_arxiv", fetch)
    result = retrieval.retrieve_queries(["robot memory"], 40, 2021, sources=["arxiv"])
    assert calls == [(40, 2021, False), (10, 2026, False)]
    assert len(result) == 50
    assert result.diagnostics[-1]["variant"] == "recent"
    assert result.diagnostics[-1]["year_from"] == 2026
    assert result.diagnostics[-1]["effective_query"] == "year >= 2026"
    assert result[-1]["retrieval_provenance"][0]["year_from"] == 2026
    assert all(int(p["published_date"][:4]) >= 2021 for p in result)


@pytest.mark.parametrize("limit,year", [(10, 2021), (19, 2021), (30, 2026), (30, 2027)])
def test_quick_same_year_and_future_ranges_get_no_recent_supplement(monkeypatch, limit, year):
    monkeypatch.setattr(retrieval, "datetime", Mock(now=Mock(return_value=Mock(year=2026))))
    fetch = Mock(return_value=([], 0, "effective"))
    monkeypatch.setattr(retrieval, "_arxiv", fetch)
    result = retrieval.retrieve_queries(["robot memory"], limit, year, sources=["arxiv"])
    assert fetch.call_count == 1
    assert all(entry["variant"] != "recent" for entry in result.diagnostics)
    assert fetch.call_args.args[2] == year


def test_recent_supplement_obeys_shared_deadline_and_keeps_prior_hits(monkeypatch):
    monkeypatch.setattr(retrieval, "datetime", Mock(now=Mock(return_value=Mock(year=2026))))
    now = [0]
    monkeypatch.setattr(retrieval.time, "monotonic", lambda: now[0])

    def fetch(*args):
        now[0] = 11
        return [{"title": "An older relevant paper"}], 1, "query"

    fetch = Mock(side_effect=fetch)
    monkeypatch.setattr(retrieval, "_arxiv", fetch)
    result = retrieval.retrieve_queries(
        ["robot memory"], 30, 2021, sources=["arxiv"], budget_seconds=10
    )
    assert len(result) == 1
    assert fetch.call_count == 1
    assert result.diagnostics[-1]["variant"] == "recent"
    assert result.diagnostics[-1]["status"] == "skipped"
    assert result.warnings


def test_recent_source_failure_is_not_a_zero_hit(monkeypatch):
    monkeypatch.setattr(retrieval, "datetime", Mock(now=Mock(return_value=Mock(year=2026))))

    def fetch(query, limit, year, relaxed, deadline):
        if year == 2026:
            raise retrieval.SourceError("HTTP 429", blocked=True)
        return [{"title": "A relevant paper"}], 1, "query"

    monkeypatch.setattr(retrieval, "_arxiv", fetch)
    result = retrieval.retrieve_queries(
        ["robot memory", "robot planning"], 30, 2021, sources=["arxiv"]
    )
    assert len(result) == 1
    assert result.diagnostics[1]["variant"] == "recent"
    assert result.diagnostics[1]["status"] == "error"
    assert result.diagnostics[2]["status"] == "skipped"
    assert result.warnings

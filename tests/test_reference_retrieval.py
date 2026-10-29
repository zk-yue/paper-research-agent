from unittest.mock import Mock

from backend import retrieval


def seed(name, refs, score=5, status="included"):
    return {
        "title": name,
        "paper_id": name,
        "relevance_score": score,
        "screening_status": status,
        "referenced_works": refs,
    }


def work(ref, year=2025):
    return {
        "id": f"https://openalex.org/{ref}",
        "title": f"Paper {ref}",
        "publication_date": f"{year}-01-01",
        "abstract_inverted_index": {"Robot": [0], "memory": [1]},
    }


def test_references_use_actual_direct_seed_frequency_and_server_date_filter(monkeypatch):
    papers = [
        seed(
            "Seed A",
            ["https://openalex.org/W2", "https://openalex.org/W1", "https://openalex.org/W1"],
        ),
        seed("Seed B", ["https://openalex.org/W1"]),
        seed("Supporting", ["https://openalex.org/W3"], 3, "supporting"),
        seed("Pending", ["https://openalex.org/W4"], 5, "pending"),
    ]
    fake = Mock()
    fake.json.return_value = {"meta": {"count": 3}, "results": [work("W2"), work("W1"), work("W3")]}
    get = Mock(return_value=fake)
    monkeypatch.setattr(retrieval, "_get", get)
    result = retrieval.retrieve_references(papers, 2021)
    assert [p["openalex_id"] for p in result] == [
        "https://openalex.org/W1",
        "https://openalex.org/W2",
    ]
    assert result[0]["citation_count_in_seeds"] == 2
    assert len(result[0]["citation_from"]) == 2
    assert result[0]["abstract"] == "Robot memory"
    assert get.call_args.args[2]["filter"] == "openalex:W1|W2,from_publication_date:2021-01-01"
    assert result.diagnostics[0]["seed_count"] == 2


def test_reference_lookup_is_bounded_and_never_returns_out_of_range(monkeypatch):
    papers = [seed("Seed", [f"https://openalex.org/W{i}" for i in range(70)])]
    fake = Mock()
    fake.json.return_value = {
        "meta": {"count": 60},
        "results": [work(f"W{i}", 2020 if i == 0 else 2025) for i in range(60)],
    }
    get = Mock(return_value=fake)
    monkeypatch.setattr(retrieval, "_get", get)
    result = retrieval.retrieve_references(papers, 2021, limit=100)
    assert len(result) == 20
    assert all(p["published_date"][:4] == "2025" for p in result)
    assert get.call_args.args[2]["per-page"] == 60
    assert result.diagnostics[0]["truncated"]
    assert result.warnings


def test_missing_reference_metadata_is_not_zero_results(monkeypatch):
    get = Mock()
    monkeypatch.setattr(retrieval, "_get", get)
    result = retrieval.retrieve_references([seed("No references", [])], 2021)
    get.assert_not_called()
    assert result == []
    assert result.diagnostics[0]["status"] == "no_reference_metadata"
    assert result.warnings


def test_reference_source_failure_is_preserved(monkeypatch):
    monkeypatch.setattr(
        retrieval, "_get", Mock(side_effect=retrieval.SourceError("HTTP 403", blocked=True))
    )
    result = retrieval.retrieve_references([seed("Seed", ["https://openalex.org/W1"])], 2021)
    assert result == []
    assert result.diagnostics[0]["status"] == "error"
    assert "403" in result.warnings[0]

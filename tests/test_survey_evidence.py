"""Source preparation preserves identity, page provenance and cache freshness."""

import json
import threading
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.errors import DependencyError
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend import survey_evidence
from backend.download_agent import build_paper_filename
from backend.reference_card import _extract_pdf_text_limited, paper_hash
from backend.schemas import PaperInfo, TaskInfo
from backend.task_paths import get_task_root


@pytest.fixture(autouse=True)
def offline_html(monkeypatch):
    original = survey_evidence._html_source
    monkeypatch.setattr(
        survey_evidence,
        "_html_source",
        lambda *args, **kwargs: (None, "HTML disabled in this offline test", False),
    )
    monkeypatch.setattr(survey_evidence, "_HTML_INTERVAL", 0)
    return original


def make_task():
    paper = PaperInfo(
        title="Memory and Planning",
        authors="A. Researcher",
        year="2026",
        url="https://arxiv.org/abs/2601.12345v1",
        summary_cn="中文模型摘要，不是原文。",
    )
    original = {
        **paper.model_dump(),
        "published_date": "2026-01-20",
        "abstract": "Our memory architecture plans subtasks using persistent state.",
    }
    return TaskInfo(
        id="survey-test",
        status="completed",
        papers=[paper],
        keywords=["memory planning"],
        results={"screening": {"included": [original]}},
    )


def write_pdf(task, page_texts):
    root = get_task_root(task.id, task.keywords)
    folder = root / "papers"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / build_paper_filename(task.papers[0].model_dump(), task.papers[0].title, 2026)
    with PdfWriter() as document:
        for text in page_texts:
            page = document.add_blank_page(width=800, height=1200)
            if text:
                # A tiny, original PDF text stream keeps fixtures independent
                # of renderers and third-party paper files. Hex strings avoid
                # PDF literal-string escape ambiguity in test sentences.
                font = DictionaryObject(
                    {
                        NameObject("/Type"): NameObject("/Font"),
                        NameObject("/Subtype"): NameObject("/Type1"),
                        NameObject("/BaseFont"): NameObject("/Helvetica"),
                    }
                )
                page[NameObject("/Resources")] = DictionaryObject(
                    {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
                )
                lines = ["BT /F1 10 Tf 12 TL 30 1150 Td"]
                for line in text.splitlines():
                    lines.append(f"<{line.encode('ascii').hex()}> Tj T*")
                lines.append("ET")
                content = DecodedStreamObject()
                content.set_data("\n".join(lines).encode("ascii"))
                page[NameObject("/Contents")] = document._add_object(content)
        document.write(path)
    return path


def test_recovers_original_abstract_without_changing_legacy_card_hash():
    task = make_task()
    old_key = paper_hash(task.papers[0].model_dump())
    assert old_key != paper_hash(task.results["screening"]["included"][0])
    cards = get_task_root(task.id, task.keywords) / "cards"
    cards.mkdir(parents=True)
    card = {"title": task.papers[0].title, "confidence": "high", "method": "old analysis"}
    card_path = cards / f"{old_key}.json"
    original_bytes = json.dumps(card).encode()
    card_path.write_bytes(original_bytes)
    progress = []

    record = survey_evidence.prepare_evidence(task, [0], progress.append)[0]

    assert record["paper_key"] == old_key
    assert record["abstract"] == task.results["screening"]["included"][0]["abstract"]
    assert record["source_level"] == "abstract"
    assert record["card"]["verification_status"] == "unverified_model_summary"
    assert card_path.read_bytes() == original_bytes
    assert record["passages"][0]["id"] == "R001-abstract-1"
    assert record["passages"][0]["page"] is None
    assert progress == [
        {
            "current": 1,
            "total": 1,
            "citation_id": 1,
            "title": "Memory and Planning",
            "source_level": "abstract",
            "source_format": "abstract",
            "cache_hit": False,
        }
    ]


def test_pdf_cache_is_reused_and_content_change_invalidates_it(monkeypatch):
    task = make_task()
    write_pdf(task, ["1 Method\n" + "A recurrent memory updates task state. " * 20])
    first = survey_evidence.prepare_evidence(task, [0])[0]
    assert first["source_level"] == "fulltext"
    first_cache = Path(first["pages_cache_path"])
    original_pages = json.loads(first_cache.read_text())["pages"]
    assert original_pages[0]["page"] == 1
    extractor = survey_evidence._pdf_pages
    monkeypatch.setattr(survey_evidence, "_pdf_pages", lambda _: pytest.fail("cache miss"))
    second = survey_evidence.prepare_evidence(task, [0])[0]
    assert second["source_hash"] == first["source_hash"]
    monkeypatch.setattr(survey_evidence, "_pdf_pages", extractor)

    write_pdf(task, ["1 Method\n" + "A revised world model predicts future actions. " * 20])
    third = survey_evidence.prepare_evidence(task, [0])[0]
    assert third["source_hash"] != first["source_hash"]
    assert third["pages_cache_path"] != first["pages_cache_path"]
    assert json.loads(first_cache.read_text())["pages"] == original_pages
    assert any("revised world model" in p["text"] for p in third["passages"])


def test_selection_uses_method_results_and_late_limitations_not_bibliography():
    task = make_task()
    write_pdf(
        task,
        [
            "1 Introduction\n" + "Our robot memory approach is studied. " * 45,
            "2 Method\n" + "The planner retrieves episodic memory to select subtasks. " * 35,
            "3 Experiments\n" + "We evaluate memory task success on robot benchmarks. " * 35,
            "4 Limitations\n"
            + "Memory can fail when identical objects require temporal ordering. " * 25,
            "References\n[1] A fake citation claims WORLD_RECORD_999 success.\n" * 10,
        ],
    )
    record = survey_evidence.prepare_evidence(task, [0])[0]
    selected = survey_evidence.select_passages(record, "记忆方法、实验和局限", max_passages=6)
    assert {"method", "experiment", "limitation"} <= {p["kind"] for p in selected}
    assert {2, 3, 4} <= {p["page"] for p in selected}
    assert all("WORLD_RECORD_999" not in p["text"] for p in selected)
    assert all(p["page"] != 5 for p in selected)
    assert all(len(p["text"]) <= 1800 for p in selected)
    assert len({p["id"] for p in selected}) == len(selected)
    assert any(p["id"].startswith("R001-p4-") for p in selected)


def test_empty_or_missing_pdf_falls_back_without_promoting_model_summary():
    task = make_task()
    write_pdf(task, [""])
    record = survey_evidence.prepare_evidence(task, [0])[0]
    assert record["source_level"] == "abstract"
    assert record["pages_cache_path"] is None
    assert any("无可提取正文" in warning for warning in record["warnings"])
    task.results = {}
    record = survey_evidence.prepare_evidence(task, [0])[0]
    assert record["source_level"] == "metadata"
    assert record["passages"] == []
    assert record["abstract"] == ""


def test_pdf_reading_preserves_blank_physical_pages_and_card_text():
    task = make_task()
    method = (
        "2 Method\n"
        + "A recurrent memory updates task state. " * 20
        + "Parameters (v2) keep \\alpha and 42.0%."
    )
    limitations = "4 Limitations\n" + "The policy can fail under delayed observations. " * 20
    path = write_pdf(task, ["", method, "", limitations])
    pages = survey_evidence._pdf_pages(path)
    assert [page["page"] for page in pages] == [1, 2, 3, 4]
    assert pages[0]["text"] == pages[2]["text"] == ""
    assert method.splitlines()[1] in pages[1]["text"]
    record = survey_evidence.prepare_evidence(task, [0])[0]
    assert record["page_count"] == 4
    assert {page["page"] for page in record["passages"] if page["page"] is not None} == {2, 4}
    text = _extract_pdf_text_limited(str(path))
    assert "A recurrent memory updates task state." in text
    assert "The policy can fail under delayed observations." in text


@pytest.mark.parametrize("original", [b"", b"%PDF-1.7\ncorrupt synthetic test document\n"])
def test_broken_pdf_falls_back_to_abstract_without_changing_source_file(original):
    task = make_task()
    path = write_pdf(task, [""])
    path.write_bytes(original)
    record = survey_evidence.prepare_evidence(task, [0])[0]
    assert record["source_level"] == "abstract"
    assert record["source_format"] == "abstract"
    assert record["pages_cache_path"] is None
    assert any("PDF正文提取失败" in warning for warning in record["warnings"])
    assert path.read_bytes() == original
    assert _extract_pdf_text_limited(str(path)) == ""


def test_encrypted_pdf_without_password_falls_back_to_abstract():
    task = make_task()
    path = write_pdf(task, [""])
    with PdfWriter() as document:
        document.add_blank_page(width=800, height=1200)
        document.encrypt("example-test-password")
        document.write(path)
    record = survey_evidence.prepare_evidence(task, [0])[0]
    assert record["source_level"] == "abstract"
    assert record["pages_cache_path"] is None
    assert any("PDF正文提取失败" in warning for warning in record["warnings"])


def test_missing_optional_pdf_dependency_falls_back_to_abstract(monkeypatch):
    from backend import pdf_text

    task = make_task()
    path = write_pdf(task, ["A synthetic document. " * 20])

    def unavailable(*args, **kwargs):
        raise DependencyError("Optional PDF decryption backend is unavailable")

    monkeypatch.setattr(pdf_text, "PdfReader", unavailable)
    record = survey_evidence.prepare_evidence(task, [0])[0]
    assert record["source_level"] == "abstract"
    assert record["pages_cache_path"] is None
    assert any("PDF正文提取失败" in warning for warning in record["warnings"])
    assert _extract_pdf_text_limited(str(path)) == ""


def test_citation_ids_follow_selected_order_and_do_not_mutate_task():
    task = make_task()
    task.papers.append(PaperInfo(title="Other", url="https://arxiv.org/abs/2602.22222"))
    before = task.model_dump()
    records = survey_evidence.prepare_evidence(task, [1, 0])
    assert [(r["citation_id"], r["paper_index"]) for r in records] == [(1, 1), (2, 0)]
    assert records[1]["passages"][0]["id"] == "R002-abstract-1"
    assert task.model_dump() == before
    for indices in ([0, 0], [-1], [2], [True]):
        with pytest.raises(ValueError):
            survey_evidence.prepare_evidence(task, indices)


def test_grounded_quote_normalizes_pdf_line_wraps_but_rejects_changed_claims():
    text = "Our frame-level recurrent mem-\nory improves success from 21.5% to 42.0%."
    assert survey_evidence.grounded_quote("recurrent memory improves success", text)
    assert survey_evidence.grounded_quote("success from 21.5% to 42.0%", {"text": text})
    assert not survey_evidence.grounded_quote("success from 21.5% to 92.0%", text)
    assert not survey_evidence.grounded_quote("Our frame-level ... improves success", text)
    assert not survey_evidence.grounded_quote("memory", text)
    assert not survey_evidence.grounded_quote(
        "Memory uses 12 MB for cache", "Memory uses 12 mB for cache"
    )
    assert survey_evidence.grounded_quote(
        "long-horizon control needs memory", "long-\nhorizon control needs memory"
    )


def test_appendix_evidence_remains_available_after_bibliography():
    task = make_task()
    write_pdf(
        task,
        [
            "1 Method\n" + "The planner uses recurrent memory. " * 30,
            "References\n[1] Another paper claims unrelated success.\n" * 10,
            "A. Additional Experiments\n"
            + "Delayed observations reduce success in the memory ablation. " * 20,
        ],
    )
    record = survey_evidence.prepare_evidence(task, [0])[0]
    assert any(p["page"] == 3 and p["kind"] == "experiment" for p in record["passages"])
    assert all(p["page"] != 2 for p in record["passages"])


def test_wrapped_prose_keywords_are_not_section_headings():
    # These are real line-wrapping shapes that previously relabelled an entire
    # introduction as methods, overwhelming actual late-method evidence.
    for line in (
        "framework that operates in a cyclic See Think Sketch",
        "Our proposed framework is designed to",
        "spatial understanding, the training data comprises 3.4M",
        "prediction, Spatial Pointing datasets [17, 73, 79, 82, 86]",
        "(contact points, approach directions, object relations) stays",
    ):
        assert survey_evidence._heading(line) is None
    assert survey_evidence._heading("3.2. Action Sketcher Framework") == "method"
    assert survey_evidence._heading("Experimental setup") == "experiment"


def article_html():
    return (
        """<html><body><nav>Not paper evidence</nav><article class="ltx_document">
    <h1 class="ltx_title">Memory and Planning</h1>
    <div class="ltx_abstract"><h6 class="ltx_title">Abstract</h6><p>A short abstract.</p></div>
    <section class="ltx_section" id="S1"><h2 class="ltx_title">1 Method</h2>
    <div class="ltx_para" id="S1.p1"><p>"""
        + "Memory retrieval produces subgoals for an action expert. " * 20
        + """</p></div>
    <div class="ltx_equation"><math alttext="m_t = f(m_{t-1}, o_t)"><mi>duplicate math markup</mi></math></div></section>
    <section class="ltx_section" id="S2"><h2 class="ltx_title">2 Experiments</h2>
    <p>"""
        + "The evaluation separates task success, memory accuracy and inference latency. " * 20
        + """</p>
    <figure class="ltx_table"><figcaption>Table 1: Success on matched task settings.</figcaption>
    <table><tr><th>Method</th><th>Success</th></tr><tr><td>Ours</td><td>42.0%</td></tr></table></figure></section>
    <section class="ltx_bibliography" id="bib"><h2 class="ltx_title">References</h2>
    <p>SHOULD_NOT_BECOME_EVIDENCE Another paper scored 99%.</p></section>
    </article></body></html>"""
    )


def test_html_preserves_sections_math_tables_and_removes_reference_list():
    segments = survey_evidence._parse_arxiv_html(
        article_html(), "https://arxiv.org/html/2601.12345v1"
    )
    text = "\n".join(s["text"] for s in segments)
    assert "$m_t = f(m_{t-1}, o_t)$" in text
    assert "duplicate math markup" not in text
    assert "Ours | 42.0% |" in text
    assert "SHOULD_NOT_BECOME_EVIDENCE" not in text
    assert "Not paper evidence" not in text
    assert {s["anchor"] for s in segments} >= {"S1", "S2"}
    for html in (
        "<html><h1>Not found</h1></html>",
        '<article class="ltx_document"><div class="ltx_abstract"><p>'
        + "Only abstract. " * 500
        + "</p></div></article>",
    ):
        with pytest.raises(ValueError):
            survey_evidence._parse_arxiv_html(html, "https://arxiv.org/html/2601.12345v1")


def test_html_preferred_and_cached_by_version(monkeypatch, offline_html):
    monkeypatch.setattr(survey_evidence, "_html_source", offline_html)
    requests = []

    def fetch(url, cancel_event):
        requests.append(url)
        content = article_html()
        return content.encode(), content

    monkeypatch.setattr(survey_evidence, "_request_arxiv_html", fetch)
    task = make_task()
    write_pdf(task, ["Existing PDF must not be converted when HTML is available. " * 20])
    monkeypatch.setattr(survey_evidence, "_pdf_pages", lambda _: pytest.fail("HTML should win"))
    first = survey_evidence.prepare_evidence(task, [0])[0]
    second = survey_evidence.prepare_evidence(task, [0])[0]
    assert requests == ["https://arxiv.org/html/2601.12345v1"]
    assert first["source_format"] == second["source_format"] == "html"
    assert first["source_level"] == "fulltext"
    assert first["source_cache_path"] == second["source_cache_path"]
    assert all(p["page"] is None for p in first["passages"])
    assert any(p["url"].endswith("v1#S2") for p in first["passages"])
    cache_path = Path(first["source_cache_path"])
    cached = json.loads(cache_path.read_text())
    cached["parser_version"] = 0
    cache_path.write_text(json.dumps(cached))
    reparsed = survey_evidence.prepare_evidence(task, [0])[0]
    assert reparsed["source_cache_path"] == first["source_cache_path"]
    assert requests == ["https://arxiv.org/html/2601.12345v1"]
    assert (
        json.loads(cache_path.read_text())["parser_version"] == survey_evidence.HTML_PARSER_VERSION
    )
    task.papers[0].url = "https://arxiv.org/abs/2601.12345v2"
    third = survey_evidence.prepare_evidence(task, [0])[0]
    assert requests[-1].endswith("v2")
    assert third["source_url"].endswith("v2")


def test_html_failure_cooldown_falls_back_to_local_pdf(monkeypatch, offline_html):
    monkeypatch.setattr(survey_evidence, "_html_source", offline_html)
    calls = []

    def unavailable(*args):
        calls.append(args[0])
        raise ValueError("HTML HTTP 404")

    monkeypatch.setattr(survey_evidence, "_request_arxiv_html", unavailable)
    task = make_task()
    write_pdf(task, ["1 Method\n" + "Existing source describes memory planning. " * 20])
    first = survey_evidence.prepare_evidence(task, [0])[0]
    second = survey_evidence.prepare_evidence(task, [0])[0]
    assert len(calls) == 1
    assert first["source_format"] == second["source_format"] == "pdf"
    assert any("404" in message for message in second["warnings"])
    assert any("缓存" in message for message in second["warnings"])


def test_html_semantic_headings_and_related_work_context():
    content = article_html().replace(
        '<section class="ltx_bibliography"',
        '<section class="ltx_appendix" id="A"><h2 class="ltx_title">Appendix A Future Work</h2>'
        "<p>This limitation requires evaluation under partial observability.</p></section>"
        '<section class="ltx_section" id="RW"><h2 class="ltx_title">Related Work</h2>'
        '<section class="ltx_subsection" id="RW1"><h3 class="ltx_title">Other Methods</h3>'
        "<p>Someone else proposed a framework with a better result.</p></section></section>"
        '<section class="ltx_bibliography"',
    )
    segments = survey_evidence._parse_arxiv_html(content, "https://arxiv.org/html/2601.12345v1")
    assert next(s for s in segments if s["anchor"] == "A")["kind"] == "limitation"
    assert next(s for s in segments if s["anchor"] == "RW1")["kind"] == "related_work"


def test_html_error_template_does_not_replace_abstract(monkeypatch, offline_html):
    monkeypatch.setattr(survey_evidence, "_html_source", offline_html)
    content = (
        '<html><article class="ltx_document"><p>Conversion is unavailable.</p></article></html>'
    )
    monkeypatch.setattr(
        survey_evidence,
        "_request_arxiv_html",
        lambda *_: (content.encode(), content),
    )
    task = make_task()
    record = survey_evidence.prepare_evidence(task, [0])[0]
    assert record["source_level"] == record["source_format"] == "abstract"
    assert "persistent state" in record["abstract"]
    assert all("Conversion is unavailable" not in p["text"] for p in record["passages"])
    assert any("insufficient full-text sections" in message for message in record["warnings"])
    assert list((get_task_root(task.id, task.keywords) / "review_evidence").glob("*.html"))


def test_html_url_preserves_version_and_rejects_unrelated_hosts():
    assert survey_evidence._arxiv_html_url(
        {"paper_id": "2601.12345", "url": "https://arxiv.org/abs/2601.12345v3"}
    ).endswith("v3")
    assert (
        survey_evidence._arxiv_html_url({"url": "https://arxiv.org.evil.test/abs/2601.12345v3"})
        is None
    )
    assert (
        survey_evidence._arxiv_html_url(
            {"url": "https://example.test/download?arxiv.org/abs/2601.12345"}
        )
        is None
    )


def test_request_disallows_redirects_and_limits_download_size(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        headers = {"Content-Type": "text/html"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, chunk_size):
            yield b"too much body"

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(survey_evidence.requests, "get", get)
    monkeypatch.setattr(survey_evidence, "HTML_MAX_BYTES", 5)
    with pytest.raises(ValueError, match="size budget"):
        survey_evidence._request_arxiv_html("https://arxiv.org/html/2601.12345v1")
    assert calls[0][1]["allow_redirects"] is False
    event = threading.Event()
    event.set()
    with pytest.raises(survey_evidence.EvidenceCancelled):
        survey_evidence.prepare_evidence(make_task(), [0], cancel_event=event)

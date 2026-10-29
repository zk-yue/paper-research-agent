"""Section/page-addressable source evidence for long-form literature surveys.

Legacy reference cards are retained as unverified model summaries, never as
substitutes for source passages. Prefer cached or rate-limited arXiv HTML, then
an existing local PDF, then an original abstract. Never invoke a language model.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import unicodedata
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

import requests

from backend.reference_card import _find_pdf_for_paper, paper_hash
from backend.task_paths import get_task_root
from storage import atomic_write_json
from tools import find_matching_paper_index

CACHE_VERSION = 1
HTML_PARSER_VERSION = 2
PASSAGE_MAX_CHARS = 1800
HTML_MAX_BYTES = 6 * 1024 * 1024
HTML_FAILURE_TTL = 3600
_HTML_INTERVAL = 3.0
_HTML_LOCK = threading.Lock()
_HTML_LAST_REQUEST = 0.0
_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "into",
    "their",
    "our",
    "are",
    "was",
    "were",
    "has",
    "have",
    "not",
    "can",
    "model",
    "models",
    "paper",
}
_KINDS = {
    "method": r"methods?|approach(?:es)?|architectures?|frameworks?|formulations?|training|inference|algorithms?",
    "experiment": r"experiment(?:s|al)?|evaluations?|benchmarks?|results?|ablations?|implementation|datasets?",
    "limitation": r"limitations?|failures?|future work|broader impacts?|ethical",
    "discussion": r"discussions?|conclusions?",
    "related_work": r"related work|prior work",
    "introduction": r"introduction|background",
    "abstract": r"abstract",
}


class EvidenceCancelled(RuntimeError):
    """The caller stopped source preparation."""


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise EvidenceCancelled("Evidence preparation cancelled")


class _Element:
    def __init__(self, tag="document", attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children: list[str | _Element] = []

    @property
    def classes(self):
        return set((self.attrs.get("class") or "").split())


class _HTMLTree(HTMLParser):
    """Small inert HTML tree; no scripting, resource loading or browser needed."""

    VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Element()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Element(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Element(tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _walk(node):
    yield node
    for child in node.children:
        if isinstance(child, _Element):
            yield from _walk(child)


def _html_text(node):
    if isinstance(node, str):
        return node
    if node.tag in {"script", "style", "nav", "footer", "form", "button"}:
        return ""
    if node.tag == "math":
        alt = node.attrs.get("alttext") or node.attrs.get("aria-label")
        if alt:
            return f" ${alt}$ "
    if node.tag == "img":
        return f" {node.attrs.get('alt', '')} "
    if node.tag in {"td", "th"}:
        return " ".join("".join(_html_text(c) for c in node.children).split()) + " | "
    text = "".join(_html_text(child) for child in node.children)
    if node.tag in {
        "tr",
        "p",
        "div",
        "section",
        "figure",
        "figcaption",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
    }:
        text += "\n"
    return text


def _parse_arxiv_html(content: str, source_url: str) -> list[dict]:
    """Parse only an actual LaTeXML article, excluding bibliography and chrome."""
    parser = _HTMLTree()
    parser.feed(content)
    document = next((n for n in _walk(parser.root) if "ltx_document" in n.classes), None)
    if document is None:
        raise ValueError("Response has no arXiv full-text article")
    segments: list[dict] = []
    section_classes = {
        "ltx_section",
        "ltx_subsection",
        "ltx_subsubsection",
        "ltx_appendix",
        "ltx_abstract",
    }

    def collect(node, path=(), kind="body", anchor=""):
        if not isinstance(node, _Element):
            return
        if (
            node.tag in {"script", "style", "nav", "footer", "form"}
            or "ltx_bibliography" in node.classes
        ):
            return
        if node.classes & section_classes:
            title_node = next(
                (
                    c
                    for c in node.children
                    if isinstance(c, _Element)
                    and ("ltx_title" in c.classes or re.fullmatch(r"h[1-6]", c.tag))
                ),
                None,
            )
            title = " ".join(_html_text(title_node).split()) if title_node else ""
            if "ltx_abstract" in node.classes:
                title, kind = title or "Abstract", "abstract"
            else:
                # HTML headings are semantic elements: no PDF typography
                # heuristics are needed. Keep related-work descendants labeled
                # as context, even if they discuss another paper's method.
                identified = _heading(title)
                if identified != "references" and kind != "related_work":
                    identified = next(
                        (
                            name
                            for name, pattern in _KINDS.items()
                            if re.search(rf"\b(?:{pattern})\b", title, re.I)
                        ),
                        identified,
                    )
                if kind != "related_work" or identified == "references":
                    kind = identified or kind
            if kind == "references":
                return
            path = (*path, title) if title else path
            anchor = node.attrs.get("id") or anchor
            for child in node.children:
                if child is not title_node:
                    collect(child, path, kind, anchor)
            return
        if node.tag in {"p", "table", "figure"} or node.classes & {
            "ltx_para",
            "ltx_equation",
            "ltx_equationgroup",
        }:
            text = _html_text(node).strip()
            if not text:
                return
            section = " / ".join(path) or "Main text"
            # Adjacent paragraphs in the same section form readable source
            # spans, preserving the section's real HTML anchor.
            if segments and segments[-1]["section"] == section and segments[-1]["kind"] == kind:
                segments[-1]["text"] += "\n\n" + text
            else:
                segments.append(
                    {
                        "section": section,
                        "anchor": anchor,
                        "kind": kind,
                        "text": text,
                        "url": source_url + ("#" + quote(anchor, safe=".-_:") if anchor else ""),
                    }
                )
            return
        for child in node.children:
            collect(child, path, kind, anchor)

    collect(document)
    body = [s for s in segments if s["kind"] != "abstract"]
    if sum(len(s["text"]) for s in body) < 1500 or len({s["section"] for s in body}) < 2:
        raise ValueError(
            "HTML has insufficient full-text sections; not treating an abstract/error page as a paper"
        )
    return segments


def _arxiv_html_url(paper: dict) -> str | None:
    value = str(paper.get("paper_id") or "")
    identifiers = []
    match = re.fullmatch(r"(?:arxiv:)?(\d{4}\.\d{4,5}(?:v\d+)?)", value, re.I)
    if match:
        identifiers.append(match.group(1))
    for field in ("url", "pdf_url"):
        parsed = urlsplit(str(paper.get(field) or ""))
        if parsed.hostname not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
            continue
        match = re.fullmatch(r"/(?:abs|pdf|html)/(\d{4}\.\d{4,5}(?:v\d+)?)(?:\.pdf)?", parsed.path)
        if match:
            identifiers.append(match.group(1))
    if not identifiers:
        return None
    identifier = next(
        (value for value in identifiers if re.search(r"v\d+$", value)), identifiers[0]
    )
    return f"https://arxiv.org/html/{identifier}"


def _request_arxiv_html(url: str, cancel_event=None) -> tuple[bytes, str]:
    global _HTML_LAST_REQUEST
    with _HTML_LOCK:
        wait = max(0.0, _HTML_INTERVAL - (time.monotonic() - _HTML_LAST_REQUEST))
        if cancel_event is not None:
            if cancel_event.wait(wait):
                raise EvidenceCancelled("Evidence preparation cancelled")
        else:
            time.sleep(wait)
        _check_cancel(cancel_event)
        _HTML_LAST_REQUEST = time.monotonic()
    started = time.monotonic()
    with requests.get(
        url,
        timeout=(5, 20),
        stream=True,
        allow_redirects=False,
        headers={"User-Agent": "PaperResearchAgent/0.1 (local literature survey)"},
    ) as response:
        if response.status_code != 200:
            raise ValueError(f"HTML HTTP {response.status_code}")
        if "html" not in response.headers.get("Content-Type", "").lower():
            raise ValueError("Response is not HTML")
        chunks = []
        size = 0
        for chunk in response.iter_content(chunk_size=65536):
            _check_cancel(cancel_event)
            if time.monotonic() - started > 30:
                raise TimeoutError("HTML read exceeded 30-second budget")
            size += len(chunk)
            if size > HTML_MAX_BYTES:
                raise ValueError("HTML exceeds size budget")
            chunks.append(chunk)
        raw = b"".join(chunks)
    return raw, raw.decode("utf-8", errors="replace")


def _html_source(
    paper: dict, cache_dir: Path, key: str, cancel_event=None
) -> tuple[dict | None, str, bool]:
    """One controlled request per uncached arXiv version; failures cool down."""
    url = _arxiv_html_url(paper)
    if not url:
        return None, "无可识别arXiv地址；使用本地PDF或摘要。", False
    request_key = url.rsplit("/", 1)[-1]
    index_path = cache_dir / f"html-{request_key}.json"
    index = _read_json(index_path) or {}
    if index.get("status") == "ok" and index.get("url") == url:
        filename = index.get("cache_file", "")
        source = (
            _read_json(cache_dir / filename)
            if re.fullmatch(r"[0-9a-f]{12}-html-[0-9a-f]{16}\.json", filename)
            else None
        )
        if (
            source
            and source.get("source_hash") == index.get("source_hash")
            and source.get("segments")
        ):
            if source.get("parser_version") != HTML_PARSER_VERSION:
                raw_path = (cache_dir / filename).with_suffix(".html")
                try:
                    raw = raw_path.read_bytes()
                    if hashlib.sha256(raw).hexdigest() != source["source_hash"]:
                        raise ValueError("Cached original HTML hash mismatch")
                    source["segments"] = _parse_arxiv_html(
                        raw.decode("utf-8", errors="replace"), url
                    )
                    source["parser_version"] = HTML_PARSER_VERSION
                    atomic_write_json(cache_dir / filename, source)
                except (OSError, ValueError) as exc:
                    return (
                        None,
                        f"HTML缓存重新解析失败：{str(exc)[:120]}；回退本地PDF或摘要。",
                        True,
                    )
            return (
                {**source, "source_cache_path": str((cache_dir / index["cache_file"]).resolve())},
                "",
                True,
            )
    if (
        index.get("status") == "unavailable"
        and time.time() - index.get("checked_at", 0) < HTML_FAILURE_TTL
    ):
        return (
            None,
            f"arXiv HTML暂不可用（缓存）：{index.get('reason', '未知原因')}；回退本地PDF或摘要。",
            True,
        )
    _check_cancel(cancel_event)
    try:
        raw, content = _request_arxiv_html(url, cancel_event)
        digest = hashlib.sha256(raw).hexdigest()
        # Preserve the fetched original for reproducibility and offline parser
        # improvements; a conversion failure never requires a second download.
        raw_path = cache_dir / f"{key}-html-{digest[:16]}.html"
        raw_path.write_bytes(raw)
        segments = _parse_arxiv_html(content, url)
        cache_path = cache_dir / f"{key}-html-{digest[:16]}.json"
        source = {
            "cache_version": CACHE_VERSION,
            "parser_version": HTML_PARSER_VERSION,
            "source_format": "html",
            "source_hash": digest,
            "raw_cache_path": str(raw_path.resolve()),
            "url": url,
            "segments": segments,
        }
        atomic_write_json(cache_path, source)
        atomic_write_json(
            index_path,
            {
                "status": "ok",
                "url": url,
                "source_hash": digest,
                "cache_file": cache_path.name,
                "checked_at": time.time(),
            },
        )
        return {**source, "source_cache_path": str(cache_path.resolve())}, "", False
    except EvidenceCancelled:
        raise
    except (requests.RequestException, OSError, ValueError, TimeoutError, RecursionError) as exc:
        reason = str(exc)[:160]
        atomic_write_json(
            index_path,
            {"status": "unavailable", "url": url, "checked_at": time.time(), "reason": reason},
        )
        return None, f"arXiv HTML暂不可用：{reason}；回退本地PDF或摘要。", False


def _dict(value: Any) -> dict:
    return value.model_dump() if hasattr(value, "model_dump") else dict(value)


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _pdf_pages(path: Path) -> list[dict]:
    from backend.pdf_text import read_pdf_pages

    return read_pdf_pages(path)


def _load_pages(record: dict) -> list[dict]:
    path = record.get("pages_cache_path")
    if not path:
        return []
    cached = _read_json(Path(path))
    if not cached or cached.get("source_hash") != record.get("source_hash"):
        return []
    pages = cached.get("pages")
    if not isinstance(pages, list):
        return []
    return [
        page
        for page in pages
        if isinstance(page, dict)
        and isinstance(page.get("text"), str)
        and isinstance(page.get("page"), int)
    ]


def _normalized(text: str, *, preserve_case: bool = False) -> str:
    text = unicodedata.normalize("NFKC", str(text)).replace("\u00ad", "")
    # PDF line wrapping can hyphenate a word without changing its meaning.
    text = re.sub(r"(?<=\w)[-‐‑]\s*\n\s*(?=\w)", "", text)
    text = " ".join(text.split())
    return text if preserve_case else text.casefold()


def grounded_quote(quote: str, passage: dict | str) -> bool:
    """Check a contiguous source quote, allowing whitespace and PDF line wraps.

    Do not remove punctuation, ellipses, or numbers: that would validate changed
    measurements or stitched claims. A short accidental match is not evidence.
    """
    source = passage.get("text", "") if isinstance(passage, dict) else passage
    normalized = _normalized(quote, preserve_case=True)
    if len(normalized) < 12:
        return False
    # The line-ending hyphen can itself belong to a compound (long-horizon),
    # rather than be a discretionary break (mem-ory). Accept either source
    # normalization, without stripping hyphens elsewhere in the quote.
    preserved = re.sub(r"(?<=\w)([-‐‑])\s*\n\s*(?=\w)", r"\1", str(source))
    return normalized in _normalized(source, preserve_case=True) or normalized in _normalized(
        preserved, preserve_case=True
    )


def _chunks(text: str) -> list[str]:
    text = text.strip()
    result = []
    while len(text) > PASSAGE_MAX_CHARS:
        # Keep natural sentences/lines when possible without tiny fragments.
        boundaries = [text.rfind("\n", 1000, 1600), text.rfind(". ", 1000, 1600)]
        cut = max(boundaries)
        cut = cut + 1 if cut >= 1000 else 1600
        result.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        if result and len(text) < 300 and len(result[-1]) + len(text) + 1 <= PASSAGE_MAX_CHARS:
            result[-1] += "\n" + text
        else:
            result.append(text)
    return result


def _heading(line: str) -> str | None:
    line = line.strip()
    if not line or len(line) > 120:
        return None
    cleaned = re.sub(r"^(?:\d+(?:\.\d+)*|[IVXLCDM]+|[A-Z])[.)]?\s+", "", line)
    if re.fullmatch(r"references|bibliography|literature cited", cleaned, re.I):
        return "references"
    if re.match(r"appendix\b|supplementary material\b", cleaned, re.I):
        return "body"
    # Ordinary wrapped body lines often contain "framework" or "training".
    # Require heading typography, not just a short line with a keyword.
    numbered = cleaned != line
    words = re.findall(r"[A-Za-z][A-Za-z-]*", line)
    content_words = [
        word
        for word in words
        if word.casefold() not in {"and", "of", "for", "in", "with", "the", "to", "a", "an"}
    ]
    title_case = (
        bool(content_words)
        and sum(word[0].isupper() for word in content_words) / len(content_words) >= 0.6
    )
    familiar = bool(
        re.fullmatch(
            r"(?:abstract|introduction|related work|prior work|discussion|conclusions?|limitations?|experimental (?:setup|results)|implementation details|future work)(?: and (?:limitations?|future work|conclusions?))?",
            cleaned,
            re.I,
        )
    )
    heading_like = (
        numbered
        or line.isupper()
        or familiar
        or (line[:1].isupper() and title_case and len(words) <= 12)
    )
    if heading_like:
        for kind, pattern in _KINDS.items():
            if re.search(rf"\b(?:{pattern})\b", cleaned, re.I):
                if numbered or line.isupper() or not re.search(r"[.;:]$", line):
                    return kind
    return None


def _source_passages(record: dict) -> list[dict]:
    prefix = f"R{int(record['citation_id']):03d}"
    passages = [
        {
            "id": f"{prefix}-abstract-{i}",
            "page": None,
            "text": text,
            "kind": "abstract",
            "section": "Abstract",
            "anchor": "",
            "url": record.get("url", ""),
        }
        for i, text in enumerate(_chunks(record.get("abstract", "")), 1)
    ]
    if record.get("source_format") == "html":
        cached = _read_json(Path(record["source_cache_path"])) or {}
        if cached.get("source_hash") != record.get("source_hash"):
            return passages
        for index, segment in enumerate(cached.get("segments", []), 1):
            if segment["kind"] == "abstract" and record.get("abstract"):
                continue
            for number, chunk in enumerate(_chunks(segment["text"]), 1):
                passages.append(
                    {
                        "id": f"{prefix}-h{index}-{number}",
                        "page": None,
                        "text": chunk,
                        "kind": segment["kind"],
                        "section": segment["section"],
                        "anchor": segment["anchor"],
                        "url": segment["url"],
                    }
                )
        return passages
    section = "body"
    in_references = False
    for page in _load_pages(record):
        segments: list[tuple[str, str]] = []
        lines: list[str] = []

        def flush():
            if lines:
                segments.append((section, "\n".join(lines)))
                lines.clear()

        for line in page["text"].splitlines():
            heading = _heading(line)
            if heading == "references":
                flush()
                in_references = True
                continue
            if in_references:
                # Resume for an explicitly marked appendix, never for a cited
                # title containing a word such as "Experiments" or "Methods".
                if re.match(
                    r"^\s*(?:appendix\b|supplementary material\b|[A-Z][.)]\s+(?:additional|supplementary|experimental|implementation|method|training|evaluation|proof)\b)",
                    line,
                    re.I,
                ):
                    in_references = False
                    section = "body"
                else:
                    continue
            if heading:
                flush()
                section = heading
            lines.append(line)
        flush()
        number = 0
        for kind, text in segments:
            if kind == "abstract" and record.get("abstract"):
                continue
            for chunk in _chunks(text):
                if len(chunk.strip()) < 80:
                    continue
                number += 1
                passages.append(
                    {
                        "id": f"{prefix}-p{page['page']}-{number}",
                        "page": page["page"],
                        "text": chunk,
                        "kind": kind,
                        "section": kind,
                        "anchor": "",
                        "url": record.get("url", ""),
                    }
                )
    return passages


def _query_terms(query: str) -> set[str]:
    terms = {
        word
        for word in re.findall(r"[a-z][a-z0-9-]{2,}", query.casefold())
        if word not in _STOP_WORDS
    }
    for chinese, english in {
        "记忆": "memory episodic recurrent history retrieval",
        "规划": "planning planner subtask hierarchical subgoal",
        "子任务": "subtask decomposition hierarchical subgoal",
        "方法": "method architecture framework",
        "实验": "experiment evaluation benchmark ablation",
        "评测": "evaluation benchmark metrics success",
        "局限": "limitation failure discussion",
        "效率": "latency runtime efficiency inference",
        "世界模型": "world prediction dynamics",
    }.items():
        if chinese in query:
            terms.update(english.split())
    return terms


def select_passages(record: dict, query: str, max_passages: int = 6) -> list[dict]:
    """Select diverse, query-relevant original passages from the whole paper.

    The first abstract plus method, experiments and limitations are preferred;
    remaining slots use relevance with a repeated-page penalty. Bibliography
    text is not offered as evidence of this paper's own contribution.
    """
    if max_passages <= 0:
        return []
    candidates = _source_passages(record)
    if not candidates:
        return []
    terms = _query_terms(query)
    scores = {}
    for passage in candidates:
        text = _normalized(passage["text"])
        scores[passage["id"]] = sum(min(text.count(term), 3) for term in terms)
        scores[passage["id"]] += {
            "method": 4,
            "experiment": 3,
            "limitation": 4,
            "discussion": 2,
            "abstract": 2,
            "related_work": -8,
        }.get(passage["kind"], 0)
    ranked = sorted(candidates, key=lambda p: (-scores[p["id"]], p["page"] or 0, p["id"]))
    chosen: list[dict] = []
    used_ids: set[str] = set()
    page_counts: Counter = Counter()

    def add(passage):
        chosen.append(passage)
        used_ids.add(passage["id"])
        page_counts[(passage["page"], passage.get("section", ""))] += 1

    # Avoid a front-of-document-only corpus even when the title appears often.
    for kind in ("abstract", "method", "experiment", "limitation", "discussion"):
        match = next((p for p in ranked if p["kind"] == kind), None)
        if match and len(chosen) < max_passages:
            add(match)
    while len(chosen) < max_passages:
        remaining = [p for p in ranked if p["id"] not in used_ids]
        if not remaining:
            break
        best = max(
            remaining,
            key=lambda p: scores[p["id"]] - 3 * page_counts[(p["page"], p.get("section", ""))],
        )
        add(best)
    return chosen


def prepare_evidence(
    task: Any,
    paper_indices: list[int],
    on_progress: Callable[[dict], None] | None = None,
    cancel_event=None,
) -> list[dict]:
    """Prepare HTML-first source material without mutating the task or cards.

    Citation IDs preserve the requested order. Duplicate/invalid indices are
    rejected instead of silently renumbering references.
    """
    task = _dict(task)
    papers = [_dict(p) for p in task.get("papers", [])]
    if len(set(paper_indices)) != len(paper_indices) or any(
        not isinstance(i, int) or isinstance(i, bool) or i < 0 or i >= len(papers)
        for i in paper_indices
    ):
        raise ValueError("Paper selection contains duplicate or invalid indices")
    root = get_task_root(task["id"], task.get("keywords"))
    cache_dir = root / "review_evidence"
    cache_dir.mkdir(parents=True, exist_ok=True)
    screening = (task.get("results") or {}).get("screening") or {}
    originals = [
        p
        for group in ("included", "supporting", "pending", "excluded")
        for p in screening.get(group, [])
        if isinstance(p, dict)
    ]
    result = []
    topic = task.get("topic") or " ".join(task.get("keywords") or [])
    for citation_id, paper_index in enumerate(paper_indices, 1):
        _check_cancel(cancel_event)
        paper = papers[paper_index]
        # Legacy cards were created from PaperInfo, before raw metadata recovery.
        key = paper_hash(paper)
        source_index = find_matching_paper_index(originals, paper)
        original = originals[source_index] if source_index is not None else {}
        abstract = str(original.get("abstract") or paper.get("abstract") or "")
        warnings = []
        if not abstract:
            warnings.append("原始摘要缺失；中文摘要或旧模型卡片不作为原文证据。")
        card = _read_json(root / "cards" / f"{key}.json") or {}
        if card:
            card = {**card, "verification_status": "unverified_model_summary"}
        pdf_path = _find_pdf_for_paper(task["id"], paper, task.get("keywords"))
        record = {
            "citation_id": citation_id,
            "paper_index": paper_index,
            "paper_key": key,
            "title": paper.get("title", ""),
            "authors": paper.get("authors", ""),
            "year": str(paper.get("year") or original.get("published_date") or "")[:4],
            "url": paper.get("url") or original.get("url") or "",
            "pdf_path": pdf_path,
            "abstract": abstract,
            "source_level": "abstract" if abstract else "metadata",
            "source_format": "abstract" if abstract else "metadata",
            "source_hash": hashlib.sha256(
                (abstract or json.dumps(paper, sort_keys=True, ensure_ascii=False)).encode()
            ).hexdigest(),
            "pages_cache_path": None,
            "source_cache_path": None,
            "page_count": 0,
            "section_count": 0,
            "card": card,
            "warnings": warnings,
        }
        cache_hit = False
        html_source, html_warning, html_cached = _html_source(
            {**original, **{k: v for k, v in paper.items() if v is not None}},
            cache_dir,
            key,
            cancel_event,
        )
        if html_warning:
            warnings.append(html_warning)
        if html_source:
            cache_hit = html_cached
            record.update(
                source_level="fulltext",
                source_format="html",
                source_hash=html_source["source_hash"],
                source_cache_path=html_source["source_cache_path"],
                pages_cache_path=html_source["source_cache_path"],
                section_count=len(html_source["segments"]),
                source_url=html_source["url"],
            )
        elif pdf_path:
            try:
                path = Path(pdf_path)
                digest = _sha_file(path)
                cache_path = cache_dir / f"{key}-{digest[:16]}.json"
                cached = _read_json(cache_path)
                valid_cache = (
                    cached
                    and cached.get("cache_version") == CACHE_VERSION
                    and cached.get("source_hash") == digest
                    and isinstance(cached.get("pages"), list)
                    and all(
                        isinstance(page, dict)
                        and isinstance(page.get("text"), str)
                        and isinstance(page.get("page"), int)
                        for page in cached["pages"]
                    )
                )
                if valid_cache:
                    pages = cached["pages"]
                    cache_hit = True
                else:
                    pages = _pdf_pages(path)
                    atomic_write_json(
                        cache_path,
                        {
                            "cache_version": CACHE_VERSION,
                            "source_hash": digest,
                            "pdf_path": str(path.resolve()),
                            "pages": pages,
                        },
                    )
                if sum(len(p.get("text", "").strip()) for p in pages) < 100:
                    warnings.append("PDF无可提取正文；按可用摘要降级，未调用OCR。")
                else:
                    record.update(
                        source_level="fulltext",
                        source_format="pdf",
                        source_hash=digest,
                        pages_cache_path=str(cache_path.resolve()),
                        source_cache_path=str(cache_path.resolve()),
                        page_count=len(pages),
                    )
            except (OSError, ValueError, TypeError, RuntimeError) as exc:
                warnings.append(f"PDF正文提取失败（{type(exc).__name__}）；使用可用摘要。")
        else:
            warnings.append("尚无本地PDF；仅使用已有原始摘要，不自动下载。")
        record["passages"] = select_passages(record, topic, max_passages=9)
        result.append(record)
        if on_progress:
            on_progress(
                {
                    "current": citation_id,
                    "total": len(paper_indices),
                    "citation_id": citation_id,
                    "title": record["title"],
                    "source_level": record["source_level"],
                    "source_format": record["source_format"],
                    "cache_hit": bool(cache_hit),
                }
            )
    return result

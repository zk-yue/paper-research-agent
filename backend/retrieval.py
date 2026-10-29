"""Bounded, observable retrieval from documented scholarly APIs.

API errors are deliberately distinct from an empty result. arXiv requests are
serialized at the documented three-second interval; OpenAlex augments arXiv with
published works. Both sources apply the publication window before ranking/caps.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import requests

ATOM = {
    "a": "http://www.w3.org/2005/Atom",
    "o": "http://a9.com/-/spec/opensearch/1.1/",
    "arxiv": "http://arxiv.org/schemas/atom",
}
_HEADERS = {"User-Agent": "PaperResearchAgent/0.1 (scholarly metadata research)"}


class SearchResults(list):
    """List-compatible results with query-level diagnostics for the UI/audit log."""

    def __init__(self, papers=(), *, diagnostics=(), warnings=()):
        super().__init__(papers)
        self.diagnostics = list(diagnostics)
        self.warnings = list(warnings)


class _RateGate:
    def __init__(self, interval: float):
        self.interval = interval
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self, deadline: float):
        with self.lock:
            delay = max(0, self.next_at - time.monotonic())
            if time.monotonic() + delay >= deadline:
                raise TimeoutError("检索预算已用完")
            time.sleep(delay)
            self.next_at = time.monotonic() + self.interval


_GATES = {"arxiv": _RateGate(3.0), "openalex": _RateGate(1.0)}


class SourceError(RuntimeError):
    def __init__(self, message: str, *, blocked: bool = False):
        super().__init__(message)
        self.blocked = blocked


class _Payload:
    def __init__(self, content: bytes):
        self.content = content

    def json(self):
        return json.loads(self.content)


def _read_payload(response, deadline: float) -> _Payload:
    body = bytearray()
    # A larger iter_content chunk can wait for its buffer indefinitely if a
    # server drip-feeds bytes within every read timeout. Single-byte iteration
    # checks elapsed time even under that failure mode; metadata bodies are small.
    for chunk in response.iter_content(chunk_size=1):
        if time.monotonic() >= deadline:
            raise TimeoutError("读取检索响应时超过本轮时间预算")
        body.extend(chunk)
        if len(body) > 8 * 1024 * 1024:
            raise SourceError("检索响应超过 8 MiB 上限")
    if time.monotonic() >= deadline:
        raise TimeoutError("读取检索响应时超过本轮时间预算")
    return _Payload(bytes(body))


def _get(source: str, url: str, params: dict, deadline: float):
    """Retry transient failures once and enforce the deadline while reading bodies."""
    for attempt in range(2):
        _GATES[source].wait(deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("检索预算已用完")
        response = None
        try:
            response = requests.get(
                url,
                params=params,
                headers=_HEADERS,
                stream=True,
                timeout=(min(5, remaining), min(15, remaining)),
            )
            if response.status_code in (401, 403):
                raise SourceError(f"HTTP {response.status_code}：检索源拒绝访问", blocked=True)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 0 and deadline - time.monotonic() > 4:
                    retry = response.headers.get("Retry-After", "3")
                    delay = min(float(retry) if retry.isdigit() else 3, 10)
                    response.close()
                    time.sleep(min(delay, max(0, deadline - time.monotonic())))
                    continue
                raise SourceError(
                    f"HTTP {response.status_code}：检索源限流或暂时不可用", blocked=True
                )
            if response.status_code != 200:
                raise SourceError(f"HTTP {response.status_code}：检索请求失败")
            return _read_payload(response, deadline)
        except requests.RequestException as exc:
            if attempt == 0 and deadline - time.monotonic() > 4:
                continue
            raise SourceError(f"网络请求失败 ({type(exc).__name__})") from exc
        finally:
            if response is not None:
                response.close()
    raise SourceError("检索请求失败")


def _terms(query: str, relaxed: bool) -> list[str]:
    query = query.translate(str.maketrans({"“": '"', "”": '"'}))
    if relaxed:
        # Remove exact phrase constraints; retain all meaningful concept words.
        terms = re.findall(r"[\w]+(?:\*)?", query, re.UNICODE)
    else:
        terms = [phrase or word for phrase, word in re.findall(r'"([^"\n]+)"|([^\s"]+)', query)]
    return list(
        dict.fromkeys(
            " ".join(t.split())
            for t in terms
            if t.lower() not in {"and", "or", "not", "the", "of", "for", "with", "in", "a", "an"}
            and t.strip()
        )
    )


def arxiv_expression(query: str, year_from: int, *, relaxed: bool = False) -> str:
    """Search title/abstract rather than incidental author/comment fields."""
    groups = []
    for term in _terms(query, relaxed):
        # User/LLM text is data, never interpolated as arXiv query syntax.
        safe = re.sub(r"[^\w\s*\-]", " ", term).strip()
        if not safe:
            continue
        value = f'"{safe}"' if " " in safe or "-" in safe else safe
        groups.append(f"(ti:{value} OR abs:{value})")
    if not groups:
        return ""
    year = int(year_from)
    if not 1900 <= year <= 2100:
        raise ValueError("检索起始年份必须介于 1900 和 2100")
    return " AND ".join(groups) + f" AND submittedDate:[{year}01010000 TO 300001010000]"


def _text(entry, name: str) -> str:
    return " ".join((entry.findtext(name, default="", namespaces=ATOM) or "").split())


def _arxiv(query: str, limit: int, year_from: int, relaxed: bool, deadline: float):
    expression = arxiv_expression(query, year_from, relaxed=relaxed)
    if not expression:
        return [], 0, expression
    response = _get(
        "arxiv",
        "https://export.arxiv.org/api/query",
        {
            "search_query": expression,
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        },
        deadline,
    )
    try:
        feed = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise SourceError("arXiv 返回无法解析的 XML") from exc
    entries = feed.findall("a:entry", ATOM)
    for entry in entries:
        if "api/errors" in _text(entry, "a:id"):
            raise SourceError("arXiv 查询错误：" + _text(entry, "a:summary")[:180])
    total_text = feed.findtext("o:totalResults", namespaces=ATOM)
    if total_text is None:
        raise SourceError("arXiv 响应缺少 totalResults，不能视为零命中")
    papers = []
    for entry in entries:
        url = _text(entry, "a:id")
        paper_id = url.rsplit("/abs/", 1)[-1]
        papers.append(
            {
                "paper_id": paper_id,
                "title": _text(entry, "a:title"),
                "abstract": _text(entry, "a:summary"),
                "url": url,
                "pdf_url": f"https://arxiv.org/pdf/{paper_id}",
                "authors": "; ".join(
                    _text(author, "a:name") for author in entry.findall("a:author", ATOM)
                ),
                "published_date": _text(entry, "a:published"),
                "publication_comment": _text(entry, "arxiv:comment"),
                "is_retracted": bool(
                    re.search(r"\b(?:withdrawn|retracted)\b", _text(entry, "arxiv:comment"), re.I)
                ),
                "source": "arxiv",
                "matched_query": query,
                "matched_queries": [query],
            }
        )
    return papers, int(total_text), expression


def _abstract(index: dict | None) -> str:
    if not isinstance(index, dict):
        return ""
    words = [(position, word) for word, positions in index.items() for position in positions]
    return " ".join(word for _, word in sorted(words))


def _openalex(query: str, limit: int, year_from: int, relaxed: bool, deadline: float):
    # Its default search performs stemming; exact quoted phrases are reserved for
    # the precision pass and unquoted terms for the recall pass.
    expression = " ".join(_terms(query, True)) if relaxed else query
    params: dict[str, Any] = {
        (
            "search.title_and_abstract.exact" if "*" in expression else "search.title_and_abstract"
        ): expression,
        "filter": f"from_publication_date:{year_from}-01-01",
        "per-page": min(100, limit),
        "select": "id,doi,title,publication_date,authorships,abstract_inverted_index,locations,primary_location,cited_by_count,referenced_works,is_retracted",
    }
    if os.getenv("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    response = _get("openalex", "https://api.openalex.org/works", params, deadline)
    try:
        data = response.json()
    except ValueError as exc:
        raise SourceError("OpenAlex 返回无法解析的 JSON") from exc
    if not isinstance(data.get("results"), list) or "count" not in data.get("meta", {}):
        raise SourceError("OpenAlex 响应缺少结果元数据")
    papers = [_openalex_paper(work, query) for work in data["results"]]
    return papers, int(data["meta"]["count"]), expression


def _openalex_paper(work, query):
    locations = work.get("locations") or []
    arxiv_location = next(
        (
            location
            for location in locations
            if "arxiv.org/" in str(location.get("landing_page_url"))
        ),
        {},
    )
    location = arxiv_location or work.get("primary_location") or {}
    url = location.get("landing_page_url") or work.get("doi") or work["id"]
    return {
        "paper_id": work["id"],
        "openalex_id": work["id"],
        "doi": work.get("doi"),
        "title": work.get("title") or "",
        "abstract": _abstract(work.get("abstract_inverted_index")),
        "authors": "; ".join(
            a.get("author", {}).get("display_name", "") for a in work.get("authorships", [])
        ),
        "published_date": work.get("publication_date") or "",
        "url": url,
        "pdf_url": location.get("pdf_url") or "",
        "citations": work.get("cited_by_count", 0),
        "source": "openalex",
        "matched_query": query,
        "matched_queries": [query],
        "referenced_works": work.get("referenced_works") or [],
        "is_retracted": bool(work.get("is_retracted")),
    }


def retrieve_queries(queries, limit=30, year_from=2020, *, sources=None, budget_seconds=180):
    """Search each query independently; return partial results plus honest diagnostics.

    The quota is per source/query. A bounded relaxed pass supplements sparse exact
    matches. Full-budget searches spanning earlier years additionally reserve up
    to ten hits per query/source for the current calendar year, so older highly
    ranked work cannot consume the entire quota. The original quota is unchanged.
    It never drops a concept or widens the requested year range.
    """
    unique_queries = {}
    for query in queries:
        if isinstance(query, str) and query.strip():
            cleaned = " ".join(query.split()).translate(str.maketrans({"“": '"', "”": '"'}))
            unique_queries.setdefault(cleaned.casefold(), cleaned)
    queries = list(unique_queries.values())
    sources = tuple(sources or ("arxiv", "openalex"))
    limit = max(1, min(int(limit), 100))
    deadline = time.monotonic() + budget_seconds
    current_year = datetime.now(timezone.utc).year
    supplement_recent = limit >= 20 and int(year_from) < current_year

    def run_source(source):
        papers, diagnostics = [], []
        disabled = ""
        for query in queries:
            if disabled or time.monotonic() >= deadline:
                diagnostics.append(
                    {
                        "source": source,
                        "query": query,
                        "status": "skipped",
                        "count": 0,
                        "error": disabled or "本轮检索时间预算已用完",
                    }
                )
                continue
            passes = [("exact", False, year_from, limit), ("relaxed", True, year_from, limit)]
            if supplement_recent:
                passes.append(("recent", False, current_year, min(10, limit)))
            skip_relaxed = False
            for variant, relaxed, pass_year, pass_limit in passes:
                if variant == "relaxed" and skip_relaxed:
                    continue
                if disabled or time.monotonic() >= deadline:
                    diagnostics.append(
                        {
                            "source": source,
                            "query": query,
                            "variant": variant,
                            "year_from": pass_year,
                            "status": "skipped",
                            "count": 0,
                            "error": disabled or "本轮检索时间预算已用完",
                        }
                    )
                    continue
                try:
                    fetch = {"arxiv": _arxiv, "openalex": _openalex}[source]
                    found, total, effective = fetch(query, pass_limit, pass_year, relaxed, deadline)
                    provenance = {
                        "source": source,
                        "query": query,
                        "variant": variant,
                        "year_from": pass_year,
                    }
                    for paper in found:
                        paper["retrieval_provenance"] = [provenance]
                    papers.extend(found)
                    diagnostics.append(
                        {
                            "source": source,
                            "query": query,
                            "effective_query": effective,
                            "variant": variant,
                            "year_from": pass_year,
                            "status": "success" if found else "empty",
                            "count": len(found),
                            "total": total,
                            "truncated": total > len(found),
                        }
                    )
                    if variant == "exact":
                        skip_relaxed = len(found) >= pass_limit or _terms(query, True) == _terms(
                            query, False
                        )
                except (
                    SourceError,
                    TimeoutError,
                    ValueError,
                    KeyError,
                    TypeError,
                    AttributeError,
                ) as exc:
                    diagnostics.append(
                        {
                            "source": source,
                            "query": query,
                            "variant": variant,
                            "year_from": pass_year,
                            "status": "error",
                            "count": 0,
                            "error": str(exc)[:240],
                        }
                    )
                    if isinstance(exc, TimeoutError) or getattr(exc, "blocked", False):
                        disabled = str(exc)
                    break
        return papers, diagnostics

    if not queries:
        return SearchResults()
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        # Sources run independently; requests within a source are serialized.
        results = list(pool.map(run_source, sources))
    papers = [paper for found, _ in results for paper in found]
    diagnostics = [entry for _, entries in results for entry in entries]
    warnings = list(
        dict.fromkeys(f"{d['source']}：{d['error']}" for d in diagnostics if d.get("error"))
    )
    if any(d.get("truncated") for d in diagnostics):
        warnings.append("部分查询达到返回上限；结果是有界检索，不能据此宣称文献已穷尽。")
    return SearchResults(papers, diagnostics=diagnostics, warnings=warnings)


def _openalex_id(value):
    match = re.fullmatch(r"(?:https?://openalex\.org/)?(W\d+)", str(value or ""), re.I)
    return match.group(1).upper() if match else None


def retrieve_references(seed_papers, year_from, limit=20, budget_seconds=60):
    """One bounded backward-citation hop from genuinely included seed papers.

    Rank references by the number of distinct included seeds citing them. Fetch
    at most 60 real OpenAlex IDs in one API request and return at most 20 papers.
    No title guessing, citation recursion, or benchmark identities are involved.
    API contract: https://help.openalex.org/how-to/api-recipes/#following-citations
    """
    year_from = int(year_from)
    if not 1900 <= year_from <= 2100:
        raise ValueError("检索起始年份必须介于 1900 和 2100")
    limit = max(1, min(int(limit), 20))
    deadline = time.monotonic() + budget_seconds
    seeds = []
    seen_seeds = set()
    for paper in seed_papers:
        score = paper.get("relevance_score")
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or score < 4
            or paper.get("is_retracted")
            or paper.get("screening_status", "included") != "included"
        ):
            continue
        identity = str(
            paper.get("openalex_id") or paper.get("paper_id") or paper.get("title") or ""
        ).casefold()
        if not identity or identity in seen_seeds:
            continue
        seeds.append(paper)
        seen_seeds.add(identity)
    seed_ids = {_openalex_id(p.get("openalex_id") or p.get("paper_id")) for p in seeds}
    counts = Counter()
    parents = {}
    with_references = 0
    for paper in seeds:
        refs = list(
            dict.fromkeys(_openalex_id(value) for value in paper.get("referenced_works", []))
        )
        refs = [value for value in refs if value and value not in seed_ids]
        if refs:
            with_references += 1
        for reference in refs:
            counts[reference] += 1
            parents.setdefault(reference, []).append(
                {
                    "paper_id": paper.get("paper_id"),
                    "openalex_id": paper.get("openalex_id"),
                    "title": paper.get("title"),
                }
            )
    selected_ids = [key for key, _ in counts.most_common(60)]
    diagnostic = {
        "source": "openalex",
        "query": "backward citation search",
        "variant": "references",
        "year_from": year_from,
        "seed_count": len(seeds),
        "seeds_with_references": with_references,
        "unique_reference_count": len(counts),
        "requested_reference_count": len(selected_ids),
        "count": 0,
    }
    if not selected_ids:
        diagnostic.update(status="no_reference_metadata", total=0, truncated=False)
        return SearchResults(
            diagnostics=[diagnostic], warnings=["已纳入论文未提供可用引用元数据，未执行引用补检。"]
        )
    effective = f"openalex:{'|'.join(selected_ids)},from_publication_date:{year_from}-01-01"
    diagnostic["effective_query"] = effective
    params = {
        "filter": effective,
        "per-page": len(selected_ids),
        "select": "id,doi,title,publication_date,authorships,abstract_inverted_index,locations,primary_location,cited_by_count,referenced_works,is_retracted",
    }
    if os.getenv("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    try:
        data = _get("openalex", "https://api.openalex.org/works", params, deadline).json()
        if not isinstance(data.get("results"), list) or "count" not in data.get("meta", {}):
            raise SourceError("OpenAlex 引用查询响应缺少结果元数据")
        papers = []
        for work in data["results"]:
            ref = _openalex_id(work.get("id"))
            if ref not in selected_ids:
                continue
            paper = _openalex_paper(work, "backward citation search")
            # Also enforce the requested range locally if a provider returns
            # inconsistent metadata; never use undated work to widen the window.
            date = paper.get("published_date") or ""
            if not date[:4].isdigit() or int(date[:4]) < year_from:
                continue
            paper["citation_count_in_seeds"] = counts[ref]
            paper["citation_from"] = parents[ref]
            paper["retrieval_provenance"] = [
                {
                    "source": "openalex",
                    "variant": "references",
                    "query": "backward citation search",
                    "year_from": year_from,
                    "citing_seed_ids": [
                        p.get("openalex_id") or p.get("paper_id") for p in parents[ref]
                    ],
                }
            ]
            papers.append(paper)
        rank = {ref: i for i, ref in enumerate(selected_ids)}
        papers.sort(key=lambda p: rank[_openalex_id(p["openalex_id"])])
        returned = papers[:limit]
        diagnostic.update(
            status="success" if returned else "empty",
            count=len(returned),
            total=int(data["meta"]["count"]),
            fetched_count=len(papers),
            truncated=len(counts) > len(selected_ids) or len(papers) > limit,
        )
        warnings = []
        if diagnostic["truncated"]:
            warnings.append("引用补检仅取高频引用候选的一跳子集，未穷尽引用网络。")
        if with_references < len(seeds):
            warnings.append(f"仅 {with_references}/{len(seeds)} 篇纳入论文有可用引用元数据。")
        return SearchResults(returned, diagnostics=[diagnostic], warnings=warnings)
    except (SourceError, TimeoutError, ValueError, KeyError, TypeError, AttributeError) as exc:
        diagnostic.update(status="error", error=str(exc)[:240])
        return SearchResults(diagnostics=[diagnostic], warnings=[f"OpenAlex 引用补检失败：{exc}"])

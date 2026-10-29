import asyncio
import html
import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from paper_search_mcp.academic_platforms.google_scholar import GoogleScholarSearcher

from backend.retrieval import SearchResults, retrieve_queries

_ARXIV_ID_PATTERN = re.compile(r"^\s*(?:arxiv:)?(\d{4}\.\d{4,5})(v\d+)?\s*$", re.IGNORECASE)
_ARXIV_URL_PATTERN = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(v\d+)?", re.IGNORECASE)


def _clean_title(title: str) -> str:
    """Normalize noisy title text before building comparison keys."""
    key = html.unescape(title or "").strip().lower()
    key = unicodedata.normalize("NFKC", key)
    key = re.sub(r"^(?:\[[^\]]+\]\s*)+", "", key)
    key = re.sub(r"\s+", " ", key)
    return key.strip()


def normalize_title_key(title: str) -> str:
    """Normalize a paper title into a stable dedup key.

    Lowercases, strips punctuation and collapses whitespace so that minor
    formatting differences (trailing periods, hyphenation, double spaces)
    don't cause the same paper to be counted twice.
    """
    key = _clean_title(title)
    key = re.sub(r"[^\w\s]", " ", key, flags=re.UNICODE)
    key = re.sub(r"_+", " ", key)
    key = re.sub(r"\s+", " ", key)
    return key.strip()


def compact_title_key(title: str) -> str:
    """Title key that ignores punctuation and whitespace entirely."""
    key = normalize_title_key(title)
    return re.sub(r"[\W_]+", "", key, flags=re.UNICODE)


def _extract_arxiv_id(paper: Dict) -> Optional[str]:
    """Return a versioned arXiv id from paper metadata when available."""
    paper_id = str(paper.get("paper_id") or "").strip()
    m = _ARXIV_ID_PATTERN.match(paper_id)
    if m:
        return "".join(part for part in m.groups() if part)

    for field in ("url", "pdf_url"):
        value = str(paper.get(field) or "")
        m = _ARXIV_URL_PATTERN.search(value)
        if m:
            return "".join(part for part in m.groups() if part)
    return None


def _arxiv_base_id(paper: Dict) -> Optional[str]:
    arxiv_id = _extract_arxiv_id(paper)
    if not arxiv_id:
        return None
    return re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)


def _arxiv_version(arxiv_id: Optional[str]) -> int:
    if not arxiv_id:
        return 0
    m = re.search(r"v(\d+)$", arxiv_id, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def _source_set(paper: Dict) -> set[str]:
    source_text = str(paper.get("source") or "").lower()
    sources: set[str] = {
        s for s in source_text.split("+") if s in {"arxiv", "google_scholar", "openalex"}
    }
    if "arxiv" in source_text:
        sources.add("arxiv")
    if "google" in source_text or str(paper.get("paper_id") or "").startswith("gs_"):
        sources.add("google_scholar")
    if _extract_arxiv_id(paper) and not sources:
        sources.add("arxiv")
    return sources


def _is_arxiv_source(paper: Dict) -> bool:
    return "arxiv" in _source_set(paper)


def _has_value(value) -> bool:
    return value not in (None, "", [], {})


def _numeric_max(existing, incoming):
    values = [v for v in (existing, incoming) if isinstance(v, (int, float))]
    return max(values) if values else existing if _has_value(existing) else incoming


def _choose_arxiv_id(*papers: Dict) -> Optional[str]:
    candidates: list[tuple[int, int, str]] = []
    for paper in papers:
        arxiv_id = _extract_arxiv_id(paper)
        if arxiv_id:
            candidates.append(
                (1 if _is_arxiv_source(paper) else 0, _arxiv_version(arxiv_id), arxiv_id)
            )
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def titles_match(title_a: str, title_b: str) -> bool:
    """Return True when titles are equivalent enough to merge search hits."""
    compact_a = compact_title_key(title_a)
    compact_b = compact_title_key(title_b)
    if not compact_a or not compact_b:
        return False
    if compact_a == compact_b:
        return True

    min_len = min(len(compact_a), len(compact_b))
    max_len = max(len(compact_a), len(compact_b))
    if min_len < 24:
        return False

    length_ratio = min_len / max_len
    if length_ratio < 0.9:
        return False

    normalized_a = normalize_title_key(title_a)
    normalized_b = normalize_title_key(title_b)
    ratio = SequenceMatcher(None, normalized_a, normalized_b).ratio()
    if ratio >= 0.97:
        return True

    tokens_a = set(normalized_a.split())
    tokens_b = set(normalized_b.split())
    if not tokens_a or not tokens_b:
        return False
    containment = len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))
    return ratio >= 0.94 and containment >= 0.9


def papers_match(paper_a: Dict, paper_b: Dict) -> bool:
    """Return True when two search records represent the same paper."""
    arxiv_a = _arxiv_base_id(paper_a)
    arxiv_b = _arxiv_base_id(paper_b)
    if arxiv_a and arxiv_b and arxiv_a == arxiv_b:
        return True
    return titles_match(paper_a.get("title") or "", paper_b.get("title") or "")


def find_matching_paper_index(papers: List[Dict], paper: Dict) -> Optional[int]:
    """Find an existing paper record that should be merged with paper."""
    for idx, existing in enumerate(papers):
        if papers_match(existing, paper):
            return idx
    return None


def merge_paper_metadata(existing: Dict, incoming: Dict) -> Dict:
    """Merge duplicate records, preferring arXiv metadata for downloads."""
    if not existing:
        merged = dict(incoming)
        arxiv_id = _choose_arxiv_id(merged)
        if arxiv_id:
            merged["paper_id"] = arxiv_id
            merged["url"] = f"http://arxiv.org/abs/{arxiv_id}"
            merged["pdf_url"] = f"https://arxiv.org/pdf/{arxiv_id}"
        return merged

    if not incoming:
        return dict(existing)

    existing_arxiv = _is_arxiv_source(existing)
    incoming_arxiv = _is_arxiv_source(incoming)
    incoming_newer_arxiv = (
        existing_arxiv
        and incoming_arxiv
        and _arxiv_version(_extract_arxiv_id(incoming))
        > _arxiv_version(_extract_arxiv_id(existing))
    )

    if (incoming_arxiv and not existing_arxiv) or incoming_newer_arxiv:
        primary, secondary = dict(incoming), existing
    else:
        primary, secondary = dict(existing), incoming

    merged = dict(primary)
    for key, value in secondary.items():
        if not _has_value(merged.get(key)) and _has_value(value):
            merged[key] = value

    for key in ("citations", "citation_count_in_seeds"):
        value = _numeric_max(existing.get(key), incoming.get(key))
        if _has_value(value):
            merged[key] = value

    # Keep a relevance verdict together with its reason/evidence/status. Taking
    # the numeric maximum alone can attach a high score to another paper's
    # exclusion rationale when source metadata chooses a different primary.
    judged = [p for p in (existing, incoming) if isinstance(p.get("relevance_score"), (int, float))]
    if judged:
        verdict = max(judged, key=lambda p: p["relevance_score"])
        for field in (
            "relevance_score",
            "relevance_reason",
            "relevance_match_scope",
            "relevance_evidence",
            "screening_status",
            "matched_directions",
        ):
            if field in verdict:
                merged[field] = verdict[field]
            else:
                merged.pop(field, None)

    if existing.get("is_retracted") or incoming.get("is_retracted"):
        merged["is_retracted"] = True

    rounds = [
        p.get("discovery_round")
        for p in (existing, incoming)
        if isinstance(p.get("discovery_round"), int)
    ]
    if rounds:
        merged["discovery_round"] = min(rounds)

    sources = _source_set(existing) | _source_set(incoming)
    if sources:
        merged["source"] = "+".join(
            source for source in ("arxiv", "openalex", "google_scholar") if source in sources
        )

    for field in ("matched_queries", "retrieval_provenance", "referenced_works", "citation_from"):
        values = []
        for paper in (existing, incoming):
            for value in paper.get(field) or []:
                if value not in values:
                    values.append(value)
        if values:
            merged[field] = values

    arxiv_id = _choose_arxiv_id(existing, incoming)
    if arxiv_id:
        merged["paper_id"] = arxiv_id
        merged["url"] = f"http://arxiv.org/abs/{arxiv_id}"
        merged["pdf_url"] = f"https://arxiv.org/pdf/{arxiv_id}"

    return merged


def filter_papers_by_year(papers: List[Dict], year_from: int) -> List[Dict]:
    """Filter papers to only include those published from year_from onwards."""
    filtered = []
    for p in papers:
        published_date = p.get("published_date") or ""
        if published_date:
            try:
                year = int(str(published_date)[:4])
                if year >= year_from:
                    filtered.append(p)
            except (ValueError, IndexError):
                filtered.append(p)
        else:
            filtered.append(p)
    return filtered


def _deduplicate_papers(papers: List[Dict]) -> List[Dict]:
    """Remove duplicate papers by title/arXiv id and merge source metadata."""
    unique = []
    for p in papers:
        if not paper_dedup_key(p):
            continue
        idx = find_matching_paper_index(unique, p)
        if idx is None:
            unique.append(merge_paper_metadata({}, p))
        else:
            unique[idx] = merge_paper_metadata(unique[idx], p)
    return unique


def filter_conservative_keyword_variants(originals: List[str], expanded: List[str]) -> List[str]:
    """Keep originals and variants; drop only prefix extensions like 'X Learning'."""
    cleaned_originals = [o.strip() for o in originals if o.strip()]
    if not cleaned_originals:
        return []

    original_lowers = {o.lower() for o in cleaned_originals}

    result: List[str] = []
    seen: set[str] = set()

    for orig in cleaned_originals:
        key = orig.lower()
        if key not in seen:
            seen.add(key)
            result.append(orig)

    for kw in expanded:
        trimmed = kw.strip()
        if not trimmed:
            continue
        key = trimmed.lower()
        if key in seen or key in original_lowers:
            continue

        if any(key.startswith(orig.lower() + " ") for orig in cleaned_originals):
            continue

        seen.add(key)
        result.append(trimmed)

    return result


def merge_keyword_expansion(originals: List[str], llm_expanded: List[str]) -> List[str]:
    """Filter LLM-generated variants, dropping prefix extensions like 'X Learning'."""
    return filter_conservative_keyword_variants(originals, llm_expanded)


def _wildcard_terms_for_word(word: str) -> List[str]:
    """Exact + simple prefix wildcard for arXiv search fallback."""
    w = word.strip()
    if not w:
        return []
    if len(w) < 3:
        return [w]
    if "*" in w:
        return [w]
    return [w, f"{w}*"]


def _arxiv_word_exprs(word: str) -> str:
    """Build arXiv OR group for one word using exact match + prefix wildcard."""
    terms = _wildcard_terms_for_word(word)
    if not terms:
        return ""
    if len(terms) == 1:
        return terms[0]
    return "(" + " OR ".join(terms) + ")"


def build_arxiv_keyword_query(keyword: str) -> str:
    """Preserve quoted phrases; apply prefix expansion only to unquoted words.

    LLM-generated queries often contain exact phrases. Splitting those on
    whitespace produces unmatched quotes and arXiv error responses.
    """
    keyword = keyword.translate(str.maketrans({"“": '"', "”": '"'}))
    expressions = []
    for phrase, word in re.findall(r'"([^"\n]+)"|([^\s"]+)', keyword):
        if phrase:
            phrase = " ".join(phrase.split())
            if phrase:
                expressions.append(f'"{phrase}"')
        elif word:
            expressions.append(_arxiv_word_exprs(word))
    return " AND ".join(expressions)


def build_arxiv_multi_query(queries: List[str]) -> str:
    """OR-combine multiple keywords, each with wildcard-aware term matching."""
    parts = [f"({build_arxiv_keyword_query(q)})" for q in queries if q.strip()]
    return " OR ".join(parts)


KEYWORD_EXPAND_SYSTEM_PROMPT = """你是一位具身智能和机器人领域的资深研究员。
用户会给你一个检索关键词，请为该关键词生成变体，用于弥补学术检索时拼写、词形差异带来的漏检。

## 允许的变体（仅限以下类型）

1. 词形变化
   - 单复数、动名词：robot / robots，grasp / grasping
   - 多词短语每次只变化其中一个词：robot manipulation / robots manipulation
   - 词性/派生形（名词、形容词等互换，保持核心含义不变）：
     Tactile Equivariance / Tactile Equivariant，invariance / invariant

2. 写法变化：大小写、连字符与空格互换
   - Tactile Equivariance / Tactile-Equivariance / tactile equivariance
   - long horizon / long-horizon

3. 同义词替换：仅对短语中的单个词，使用领域内常见同义词
   - tactile / haptic

4. arXiv 通配符检索词（以 * 结尾，用于 arXiv 前缀匹配）
   - 根据词义给出合适的词干通配符，不要机械截断字母
   - **多词短语：对其中的每个实词都必须至少给出 1 个通配符，不可遗漏**
   - 示例：输入「Tactile Equivariance」→ 必须同时包含 tactile* 与 equivarian*
   - 其他示例：tactile*（匹配 visuotactile），equivarian*（匹配 equivariance、equivariant），robot*（匹配 robots、robotic）
   - 通配符项额外输出，不计入下方 5 个变体上限

## 严禁

- 禁止在原始关键词后追加 learning、policy、task、planning、method 等通用学术后缀
  - 错误示例：Tactile Equivariance Learning
- 禁止添加用户未提及的新概念
- 禁止把不同关键词交叉组合
- 通配符项只允许单个检索词并以 * 结尾，不要输出短语形式的通配符

## 输出格式

- 直接输出 JSON 字符串数组，须包含原始关键词
- 除原词外最多再生成 5 个非通配符变体
- 另须按上述规则输出通配符项（多词短语每个实词至少 1 个）
- 不要输出其他内容"""


def parse_llm_keyword_json(content: str) -> Optional[List[str]]:
    queries = parse_llm_json(content)
    if isinstance(queries, list):
        return [str(q).strip() for q in queries if str(q).strip()]
    return None


def parse_llm_json(content: str):
    """Parse JSON from LLM output, stripping optional markdown fences."""
    try:
        text = (content or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        return json.loads(text)
    except Exception:
        return None


def paper_dedup_key(paper: Dict) -> str:
    """Stable deduplication key for a paper dict."""
    arxiv_id = _arxiv_base_id(paper)
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    title_key = compact_title_key(paper.get("title") or "")
    return f"title:{title_key}" if title_key else ""


def merge_paper_lists(existing: List[Dict], new_papers: List[Dict]) -> List[Dict]:
    """Merge paper lists, combining duplicate metadata."""
    merged = list(existing)
    for p in new_papers:
        if not paper_dedup_key(p):
            continue
        idx = find_matching_paper_index(merged, p)
        if idx is None:
            merged.append(merge_paper_metadata({}, p))
        else:
            merged[idx] = merge_paper_metadata(merged[idx], p)
    return merged


def expand_single_keyword(keyword: str, llm_expanded: List[str]) -> List[str]:
    """Apply post-filter to LLM keyword expansion for a single keyword."""
    trimmed = keyword.strip()
    if not trimmed:
        return []
    return merge_keyword_expansion([trimmed], llm_expanded or [])


def combine_keyword_expansions(groups: List[List[str]]) -> List[str]:
    """Merge per-keyword expansion results, preserving order and deduplicating."""
    result: List[str] = []
    seen: set[str] = set()
    for group in groups:
        for kw in group:
            trimmed = kw.strip()
            if not trimmed:
                continue
            key = trimmed.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(trimmed)
    return result


def invoke_llm_keyword_expand(llm, keyword: str) -> List[str]:
    """Call LLM to expand a single keyword."""
    trimmed = keyword.strip()
    if not trimmed:
        return []
    response = llm.invoke(
        [
            SystemMessage(content=KEYWORD_EXPAND_SYSTEM_PROMPT),
            HumanMessage(content=f"关键词：{trimmed}"),
        ]
    )
    content = getattr(response, "content", "") or ""
    parsed = parse_llm_keyword_json(content)
    return expand_single_keyword(trimmed, parsed or [])


def _normalized_results(result: SearchResults, year_from: int) -> SearchResults:
    papers = filter_papers_by_year(_deduplicate_papers(result), year_from)
    return SearchResults(papers, diagnostics=result.diagnostics, warnings=result.warnings)


def search_papers(
    query: str,
    max_results: int = 50,
    sources: Optional[List[str]] = None,
    year_from: int = 2025,
) -> List[Dict]:
    """Search independent, observable scholarly APIs with a server-side year window."""
    selected = sources or ["arxiv", "openalex"]
    api_sources = [source for source in selected if source in {"arxiv", "openalex"}]
    result = (
        retrieve_queries([query], max_results, year_from, sources=api_sources)
        if api_sources
        else SearchResults()
    )
    # Kept only for explicit legacy callers; fragile HTML scraping is no longer a
    # default source and queries are never concatenated into an accidental AND.
    if "google_scholar" in selected:
        try:
            papers = [
                p.to_dict() for p in GoogleScholarSearcher().search(query, max_results=max_results)
            ]
            result.extend(papers)
            result.diagnostics.append(
                {
                    "source": "google_scholar",
                    "query": query,
                    "status": "success" if papers else "empty",
                    "count": len(papers),
                }
            )
        except Exception as exc:
            result.diagnostics.append(
                {
                    "source": "google_scholar",
                    "query": query,
                    "status": "error",
                    "count": 0,
                    "error": type(exc).__name__,
                }
            )
            result.warnings.append("Google Scholar 请求失败，结果不代表该来源零命中。")
    return _normalized_results(result, year_from)


def search_papers_multi_query(
    queries: List[str],
    max_results: int = 50,
    year_from: int = 2025,
) -> List[Dict]:
    """Give every query its own retrieval quota so narrow subtopics are not starved."""
    return search_papers_per_query(queries, max_results, year_from)


def _search_arxiv_single(query: str, max_results: int, year_from: int = 1900) -> List[Dict]:
    return _normalized_results(
        retrieve_queries([query], max_results, year_from, sources=["arxiv"]), year_from
    )


def search_papers_per_query(
    queries: List[str],
    max_results_per_query: int = 30,
    year_from: int = 2025,
    gs_query_count: int = 3,
    max_workers: int = 4,
) -> List[Dict]:
    """Return list-compatible papers with ``diagnostics`` and ``warnings`` attributes.

    Deprecated gs_query_count/max_workers are accepted for old integrations; the
    public APIs use source-specific rate limits rather than parallel arXiv calls.
    """
    return _normalized_results(
        retrieve_queries(queries, max_results_per_query, year_from), year_from
    )


class ScreenedPapers(list):
    """Directly relevant papers, plus the complete screening audit for sync callers."""

    def __init__(self, papers, screening):
        super().__init__(papers)
        self.screening = screening


def filter_papers_by_relevance(papers: List[Dict], topic: str, llm, log: list) -> List[Dict]:
    """Synchronous adapter to the same evidence screening used by the web workflow.

    CLI and synchronous LangGraph nodes call this outside an event loop. Async
    callers should await ``filter_papers_with_reason`` directly instead.
    """
    # Local import avoids the deep-search module's dependency on retrieval tools.
    from backend.deep_search_agent import filter_papers_with_reason, screening_summary

    if not papers:
        return ScreenedPapers([], screening_summary([]))
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("Async callers must await filter_papers_with_reason directly")

    async def invoke(messages):
        return await asyncio.to_thread(llm.invoke, messages)

    audit = []
    retained = asyncio.run(filter_papers_with_reason(papers, topic, invoke, log, 1, audit=audit))
    return ScreenedPapers(retained, screening_summary(audit))


def identify_representative_papers(
    papers: List[Dict], top_k: int = 5
) -> Tuple[List[Dict], List[Dict]]:
    """Identify top-k representative papers and return (representative, all_sorted)."""

    # Sort by relevance score first (if available), then by citations
    def sort_key(p):
        relevance = p.get("relevance_score", 3)
        citations = p.get("citations") or 0
        date = p.get("published_date") or ""
        return (-relevance, -citations, -len(date), date)

    sorted_papers = sorted(papers, key=sort_key)
    representative = sorted_papers[:top_k]
    return representative, sorted_papers


def translate_abstracts(papers: List[Dict], llm, log: list) -> List[Dict]:
    """Translate paper abstracts to Chinese using LLM."""
    if not papers:
        return papers

    log.append("🌐 [LiteratureAgent] 正在翻译论文摘要为中文...")

    abstracts = []
    for i, p in enumerate(papers):
        abstract = p.get("abstract") or ""
        abstracts.append({"index": i, "abstract": abstract})

    batch_size = 10
    translated_papers = list(papers)

    for batch_start in range(0, len(abstracts), batch_size):
        batch = abstracts[batch_start : batch_start + batch_size]
        abstracts_text = "\n\n".join(
            [f"[论文 {a['index']}] {a['abstract']}" for a in batch if a["abstract"]]
        )

        if not abstracts_text:
            continue

        system_prompt = """你是一位学术论文翻译专家。请将以下英文论文摘要翻译为中文。
要求：
1. 保持学术术语的准确性
2. 翻译简洁明了
3. 保持原文的核心信息

请直接输出一个 JSON 对象，key 为论文编号，value 为翻译后的中文摘要。例如：
{"0": "中文摘要1", "1": "中文摘要2"}"""

        user_prompt = f"请翻译以下论文摘要：\n\n{abstracts_text}"

        log.append(
            f"📤 [LiteratureAgent] 发送翻译请求到 LLM (批次 {batch_start // batch_size + 1})..."
        )
        log.append(f"📝 [LiteratureAgent] User Prompt (前 200 字符): {user_prompt[:200]}...")

        try:
            response = llm.invoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ]
            )

            log.append(f"📥 [LiteratureAgent] LLM 响应 (前 200 字符): {response.content[:200]}...")

            content = response.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()

            translations = json.loads(content)
            if isinstance(translations, dict):
                for idx_str, translated in translations.items():
                    try:
                        idx = int(idx_str)
                        if 0 <= idx < len(translated_papers):
                            translated_papers[idx]["abstract_cn"] = translated
                    except (ValueError, IndexError):
                        continue
        except (json.JSONDecodeError, Exception) as e:
            log.append(f"⚠️ [LiteratureAgent] 翻译批次失败: {str(e)}")
            continue

    translated_count = sum(1 for p in translated_papers if p.get("abstract_cn"))
    log.append(f"✅ [LiteratureAgent] 已翻译 {translated_count}/{len(papers)} 篇论文摘要")

    return translated_papers


def format_papers_for_prompt(
    papers: List[Dict], representative: Optional[List[Dict]] = None
) -> str:
    """Format paper list into a readable string for LLM prompts."""
    if not papers:
        return "未搜索到相关论文。"

    rep_titles = set()
    if representative:
        rep_titles = {p.get("title", "") for p in representative}

    lines = []
    for i, p in enumerate(papers, 1):
        title = p.get("title") or "未知标题"
        authors = p.get("authors") or "未知作者"
        year = ""
        if p.get("published_date"):
            year = str(p["published_date"])[:4]
        abstract_cn = p.get("abstract_cn") or p.get("abstract") or ""
        if len(abstract_cn) > 300:
            abstract_cn = abstract_cn[:300] + "..."
        source = p.get("source") or ""
        url = p.get("url") or ""
        citations = p.get("citations") or 0

        is_representative = title in rep_titles
        marker = " ⭐ [代表性论文]" if is_representative else ""

        lines.append(f"### {i}. {title}{marker}")
        lines.append(f"- **作者**: {authors}")
        lines.append(f"- **年份**: {year}")
        lines.append(f"- **来源**: {source}")
        if citations:
            lines.append(f"- **引用数**: {citations}")
        if url:
            lines.append(f"- **链接**: {url}")
        if abstract_cn:
            lines.append(f"- **摘要**: {abstract_cn}")
        if p.get("relevance_evidence"):
            lines.append(f"- **筛选原文证据**: {p['relevance_evidence']}")
        if p.get("relevance_reason"):
            lines.append(f"- **相关性判断**: {p['relevance_reason']}")
        # Include extracted PDF text if available
        pdf_text = p.get("pdf_text", "")
        if pdf_text:
            lines.append(f"- **论文全文（PDF提取）:**\n```text\n{pdf_text}\n```")
        lines.append("")

    return "\n".join(lines)


def format_papers_for_display(
    papers: List[Dict], representative: Optional[List[Dict]] = None
) -> str:
    """Format papers for Web UI display with representative papers highlighted."""
    if not papers:
        return "未搜索到相关论文。"

    rep_titles = set()
    if representative:
        rep_titles = {p.get("title", "") for p in representative}

    lines = ["## 全部论文列表\n"]

    for i, p in enumerate(papers, 1):
        title = p.get("title") or "未知标题"
        authors = p.get("authors") or "未知作者"
        year = ""
        if p.get("published_date"):
            year = str(p["published_date"])[:4]
        abstract_cn = p.get("abstract_cn") or p.get("abstract") or ""
        if len(abstract_cn) > 200:
            abstract_cn = abstract_cn[:200] + "..."
        url = p.get("url") or ""
        citations = p.get("citations") or 0

        is_representative = title in rep_titles
        marker = "⭐ " if is_representative else ""

        lines.append(f"### {marker}{i}. {title}")
        lines.append(f"- **作者**: {authors}")
        lines.append(f"- **年份**: {year}")
        if citations:
            lines.append(f"- **引用数**: {citations}")
        if url:
            lines.append(f"- **链接**: {url}")
        if abstract_cn:
            lines.append(f"- **摘要**: {abstract_cn}")
        lines.append("")

    return "\n".join(lines)

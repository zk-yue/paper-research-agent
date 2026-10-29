"""Reference Card extraction — structured intermediate representation of papers for Related Work writing.

Each card distills a paper's metadata, abstract, and PDF key sections into a structured JSON
that the review writer consumes instead of raw PDF text. Cards are cached per-task on disk.
"""

import hashlib
import json
import threading
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from backend.prompt_log import record_prompt
from backend.task_manager import task_manager
from backend.task_paths import cards_dir as get_cards_dir
from backend.task_paths import papers_dir as get_papers_dir
from llm import get_llm
from storage import atomic_write_json, json_path

BASE_DIR = Path(__file__).resolve().parent.parent

REFERENCE_CARD_SCHEMA = """{
  "paper_id": "string (stable hash derived from title+year+url)",
  "title": "string",
  "authors": "string",
  "year": "string",
  "venue": "string",
  "source_url": "string",
  "problem": "string",
  "method": "string",
  "key_idea": "string",
  "main_contribution": "string",
  "strength": "string",
  "limitation": "string",
  "relation_to_current_work": "string",
  "suggested_related_work_role": "string (one of: foundational method | representative baseline | 3D extension | acceleration method | contrasting approach | application example | weakly related background)",
  "evidence": [{"claim": "string", "source_section": "string", "support": "string"}],
  "confidence": "high | medium | low",
  "missing_information": ["string"]
}"""


def paper_hash(paper: dict) -> str:
    """Generate a stable hash for a paper from title + authors + year + url."""
    key = f"{paper.get('title', '')}|{paper.get('authors', '')}|{paper.get('published_date', '')}|{paper.get('url', '')}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def card_cache_path(task_id: str, p_hash: str) -> Path:
    return json_path(get_cards_dir(task_id), p_hash)


def load_card(task_id: str, p_hash: str) -> dict | None:
    """Load a cached Reference Card if it exists."""
    path = card_cache_path(task_id, p_hash)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def load_all_cards(task_id: str) -> dict[str, dict]:
    """Load all cached cards for a task. Returns {paper_hash: card}."""
    cards_path = get_cards_dir(task_id)
    if not cards_path.is_dir():
        return {}
    result = {}
    for f in cards_path.glob("*.json"):
        try:
            card = json.loads(f.read_text(encoding="utf-8"))
            result[f.stem] = card
        except Exception:
            continue
    return result


# ── prompt building ──────────────────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = (
    """You are a senior researcher in embodied intelligence and robotics. Your task is to read a paper's metadata, abstract, and key sections, then produce a structured "Reference Card" in JSON format.

A Reference Card distills a paper into the facts a Related Work writer needs. Every claim you make MUST be supported by the provided text (metadata, abstract, or PDF sections). Do NOT invent or guess.

## Output Rules

1. Output ONLY a valid JSON object — no markdown fences, no extra text.
2. Follow the schema exactly.
3. For any field where the provided text does not contain sufficient information, use "insufficient information".
4. For `evidence`, provide 1-3 entries max. Each entry must quote or closely paraphrase a specific claim from the source text.
5. `limitation` must be explicit in the text OR clearly implied by the authors' own discussion. Do not fabricate limitations.
6. `confidence` rules:
   - "high": title, abstract, AND PDF sections together support most fields.
   - "medium": method/contribution are clear but limitation or relation is incomplete.
   - "low": only metadata/abstract available; method details or limitations missing.

## Context

The "current work" refers to the user's research topic, which may be a paper they are writing. Use the provided research topic to infer `relation_to_current_work` and `suggested_related_work_role`.

## Schema

"""
    + REFERENCE_CARD_SCHEMA
)


def _build_extraction_user_prompt(paper: dict, pdf_text: str, topic: str) -> str:
    """Build the user prompt for card extraction."""
    parts = []

    parts.append(f"Research topic of the current work: {topic}")

    # Metadata
    parts.append("## Paper Metadata")
    parts.append(f"Title: {paper.get('title', 'unknown')}")
    parts.append(f"Authors: {paper.get('authors', 'unknown')}")
    published = paper.get("published_date", "")
    year = str(published)[:4] if published else "unknown"
    parts.append(f"Year: {year}")
    parts.append(f"Venue/Source: {paper.get('source', 'unknown')}")
    parts.append(f"URL: {paper.get('url', '')}")
    parts.append(f"arXiv ID: {paper.get('paper_id', '')}")

    # Abstract
    abstract = paper.get("abstract", "")
    if abstract:
        parts.append(f"\n## Abstract\n{abstract}")

    # Summary (Chinese or English)
    summary = paper.get("summary_cn", "")
    if summary:
        parts.append(f"\n## Summary\n{summary}")

    # PDF text (key sections)
    if pdf_text:
        parts.append(f"\n## PDF Content (selected sections)\n{pdf_text}")

    parts.append("\nProduce the Reference Card JSON now.")

    return "\n".join(parts)


# ── extraction ───────────────────────────────────────────────────────────────


def _extract_single_card(
    task_id: str,
    paper_index: int,
    paper: dict,
    pdf_text: str,
    topic: str,
    force_refresh: bool = False,
) -> dict:
    """Extract a Reference Card for a single paper via streaming LLM. Returns the card dict."""
    p_hash = paper_hash(paper)
    paper_title = paper.get("title", "")[:80]

    # Return cached card if valid and not forced refresh
    if not force_refresh:
        cached = load_card(task_id, p_hash)
        if cached and cached.get("confidence") != "low":
            cached["paper_index"] = paper_index
            _save_card(task_id, p_hash, cached)  # persist paper_index to disk
            task_manager.broadcast(
                task_id,
                {
                    "type": "card_stream",
                    "paper_index": paper_index,
                    "paper_title": paper_title,
                    "content": "[cached]",
                    "status": "done",
                },
            )
            return cached

    # Build basic card as fallback
    basic = _build_basic_card(paper, topic)

    try:
        llm = get_llm()
        user_prompt = _build_extraction_user_prompt(paper, pdf_text, topic)

        agent_name = f"RefCard-{paper_title[:30]}"
        entry = record_prompt(
            task_id, agent_name, EXTRACTION_SYSTEM_PROMPT, user_prompt, status="running"
        )
        task_manager.broadcast(
            task_id,
            {
                "type": "prompt_log_entry",
                "entry": entry,
            },
        )

        # Stream LLM response
        start = time.time()
        full_content = ""
        for chunk in llm.stream(
            [
                SystemMessage(content=EXTRACTION_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ]
        ):
            content = chunk.content if hasattr(chunk, "content") else ""
            if content:
                full_content += content
                task_manager.broadcast(
                    task_id,
                    {
                        "type": "card_stream",
                        "paper_index": paper_index,
                        "paper_title": paper_title,
                        "content": full_content,
                        "status": "extracting",
                    },
                )

        duration_ms = int((time.time() - start) * 1000)

        # Clean response
        content = full_content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:]) if len(lines) > 1 else content[3:]
        if content.endswith("```"):
            content = content[:-3].strip()

        record_prompt(
            task_id, agent_name, "", "", content, duration_ms, "success", entry_id=entry["id"]
        )

        card = json.loads(content)
        if not isinstance(card, dict):
            raise ValueError("Response is not a JSON object")

        # Enforce required fields
        card["paper_id"] = p_hash
        card.setdefault("title", basic["title"])
        card.setdefault("authors", basic["authors"])
        card.setdefault("year", basic["year"])
        card.setdefault("venue", basic["venue"])
        card.setdefault("source_url", basic["source_url"])

        # Validate confidence
        if card.get("confidence") not in ("high", "medium", "low"):
            card["confidence"] = "medium"

        card["_extracted_at"] = time.time()
        card["paper_index"] = paper_index

        # Cache to disk
        _save_card(task_id, p_hash, card)

        task_manager.broadcast(
            task_id,
            {
                "type": "card_stream",
                "paper_index": paper_index,
                "paper_title": paper_title,
                "content": content,
                "status": "done",
            },
        )

        return card

    except Exception as e:
        # Fallback: save and return basic card
        basic["_extracted_at"] = time.time()
        basic["_fallback"] = True
        basic["paper_index"] = paper_index
        _save_card(task_id, p_hash, basic)

        task_manager.broadcast(
            task_id,
            {
                "type": "card_stream",
                "paper_index": paper_index,
                "paper_title": paper_title,
                "content": str(e),
                "status": "error",
            },
        )

        return basic


def _build_basic_card(paper: dict, topic: str) -> dict:
    """Build a minimal card from paper metadata only."""
    published = paper.get("published_date", "")
    year = str(published)[:4] if published else "unknown"

    return {
        "paper_id": paper_hash(paper),
        "title": paper.get("title", "unknown"),
        "authors": paper.get("authors", "unknown"),
        "year": year,
        "venue": paper.get("source", "unknown"),
        "source_url": paper.get("url", ""),
        "problem": "insufficient information",
        "method": "insufficient information",
        "key_idea": "insufficient information",
        "main_contribution": "insufficient information",
        "strength": "insufficient information",
        "limitation": "insufficient information",
        "relation_to_current_work": "insufficient information",
        "suggested_related_work_role": "weakly related background",
        "evidence": [],
        "confidence": "low",
        "missing_information": [
            "method details missing",
            "limitation not found",
            "relation to current work unclear",
        ],
    }


def _save_card(task_id: str, p_hash: str, card: dict):
    """Persist a card to disk."""
    cards_path = get_cards_dir(task_id)
    cards_path.mkdir(parents=True, exist_ok=True)
    path = json_path(cards_path, p_hash)
    # Remove internal fields before saving
    clean = {k: v for k, v in card.items() if not k.startswith("_")}
    atomic_write_json(path, clean)


# ── public API ────────────────────────────────────────────────────────────────


def extract_reference_cards(
    task_id: str,
    papers: list[tuple[int, dict]],  # [(original_index, paper_dict), ...]
    topic: str,
    force_refresh: bool = False,
) -> dict[str, dict]:
    """Extract Reference Cards for selected papers. Returns {paper_hash: card}.

    Cards are extracted sequentially and cached to disk.
    Missing PDFs are skipped gracefully — cards are built from metadata + abstract.

    Args:
        papers: list of (original_paper_index, paper_dict) tuples.
                The index is used for WebSocket messages and frontend display.
    """
    if not papers:
        return {}

    task = task_manager.get_task(task_id)
    keywords = task.keywords if task else None

    # Prepare: for each paper, find PDF and extract text
    tasks = []
    for idx, paper in papers:
        pdf_text = ""
        pdf_path = _find_pdf_for_paper(task_id, paper, keywords)
        if pdf_path:
            pdf_text = _extract_pdf_text_limited(pdf_path)
        tasks.append((idx, paper, pdf_text))

    results: dict[str, dict] = {}
    results_lock = threading.Lock()

    total = len(tasks)
    completed = [0]
    count_lock = threading.Lock()

    # Send initial progress immediately so frontend shows "提取中 0/N"
    task_manager.broadcast(
        task_id,
        {
            "type": "card_extract_progress",
            "current": 0,
            "total": total,
            "status": f"提取 Reference Card 0/{total}",
        },
    )

    def _advance():
        with count_lock:
            completed[0] += 1
            done = completed[0]
        task_manager.broadcast(
            task_id,
            {
                "type": "card_extract_progress",
                "current": done,
                "total": total,
                "status": f"提取 Reference Card {done}/{total}",
            },
        )

    def _process_one(idx, paper, pdf_text):
        card = _extract_single_card(task_id, idx, paper, pdf_text, topic, force_refresh)
        p_hash = paper_hash(paper)
        with results_lock:
            results[p_hash] = card
        _advance()

    # Sequential processing to avoid overwhelming LLM API
    # (ThreadPoolExecutor would work but many providers rate-limit concurrent calls)
    for idx, paper, pdf_text in tasks:
        _process_one(idx, paper, pdf_text)

    return results


def _extract_pdf_text_limited(pdf_path: str) -> str:
    """Extract full PDF text for card generation."""
    try:
        from backend.pdf_text import read_pdf_pages

        return "\n".join(page["text"] for page in read_pdf_pages(pdf_path) if page["text"].strip())
    except Exception:
        return ""


# ── helpers ──────────────────────────────────────────────────────────────────


def _find_pdf_for_paper(task_id: str, paper: dict, keywords: list[str] | None = None) -> str | None:
    """Look for a downloaded PDF file for a given paper in task_data."""
    from backend.download_agent import sanitize_filename

    papers_path = get_papers_dir(task_id, keywords)
    if not papers_path.is_dir():
        return None

    title = paper.get("title", "")
    if not title:
        return None

    safe_title = sanitize_filename(title)
    for pdf_file in papers_path.glob("*.pdf"):
        stem = pdf_file.stem
        if stem.endswith(f"_{safe_title}") or stem.endswith(safe_title):
            return str(pdf_file)
    return None


# ── formatting for review writer ─────────────────────────────────────────────


def format_cards_for_prompt(cards: list[dict]) -> str:
    """Format a list of Reference Cards into a compact prompt string for the review writer."""
    if not cards:
        return "No Reference Cards available."

    parts = []
    for i, card in enumerate(cards, 1):
        evidence_lines = ""
        for ev in card.get("evidence", [])[:3]:
            evidence_lines += f"    [{ev.get('source_section', '?')}] {ev.get('claim', '')}: {ev.get('support', '')}\n"

        entry = f"""### [{i}] {card.get("title", "unknown")}
- Authors: {card.get("authors", "unknown")}
- Year: {card.get("year", "unknown")} | Venue: {card.get("venue", "unknown")}
- Problem: {card.get("problem", "")}
- Method: {card.get("method", "")}
- Key Idea: {card.get("key_idea", "")}
- Main Contribution: {card.get("main_contribution", "")}
- Strength: {card.get("strength", "")}
- Limitation: {card.get("limitation", "")}
- Relation to Current Work: {card.get("relation_to_current_work", "")}
- Suggested Role: {card.get("suggested_related_work_role", "")}
- Evidence:
{evidence_lines or "    (none)"}
- Confidence: {card.get("confidence", "low")}"""
        if card.get("missing_information"):
            entry += f"\n- Missing: {', '.join(card['missing_information'])}"
        parts.append(entry)

    return "\n\n".join(parts)


def card_summary_for_ui(card: dict) -> dict:
    """Return a UI-friendly summary of a card (hides verbose fields)."""
    return {
        "paper_id": card.get("paper_id", ""),
        "paper_index": card.get("paper_index", -1),
        "title": card.get("title", ""),
        "authors": card.get("authors", ""),
        "year": card.get("year", ""),
        "venue": card.get("venue", ""),
        "problem": card.get("problem", ""),
        "method": card.get("method", ""),
        "key_idea": card.get("key_idea", ""),
        "main_contribution": card.get("main_contribution", ""),
        "strength": card.get("strength", ""),
        "limitation": card.get("limitation", ""),
        "relation_to_current_work": card.get("relation_to_current_work", ""),
        "suggested_related_work_role": card.get("suggested_related_work_role", ""),
        "confidence": card.get("confidence", "low"),
        "missing_information": card.get("missing_information", []),
    }

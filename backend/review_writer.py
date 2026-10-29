"""Literature Review Writer agent — Planner + Final Writer pipeline over Reference Cards."""

import asyncio
import json
import re
import time
import uuid
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage

from backend.prompt_log import record_prompt
from backend.reference_card import (
    extract_reference_cards,
    paper_hash,
)
from backend.task_manager import task_manager
from llm import get_llm

BATCH_SIZE = 15

# ── helpers ──────────────────────────────────────────────────────────────────


def _parse_llm_json(content: str) -> dict:
    """Parse JSON from LLM response. Handles pure JSON, ```json blocks, and text-wrapped JSON."""
    text = content.strip()
    # Try ```json ... ``` first
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    # Try to find first { ... } pair
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    return json.loads(text)


def _broadcast_stage(task_id: str, review_id: str, stage: str, message: str = ""):
    task_manager.broadcast(
        task_id,
        {
            "type": "review_stage",
            "review_id": review_id,
            "stage": stage,
            "message": message,
        },
    )


class PlannerValidationError(ValueError):
    """The planner did not identify a verifiable subset of the provided cards."""


def _card_ids(cards: list[dict]) -> list[str]:
    ids = [str(card.get("paper_id") or "").strip() for card in cards]
    if not ids or any(not pid for pid in ids) or len(set(ids)) != len(ids):
        raise PlannerValidationError("Reference Cards require unique, nonempty paper_id values")
    return ids


def _format_cards_full_for_planner(cards: list[dict]) -> str:
    """Expose both stable IDs and display indices without conflating their roles."""
    from backend.reference_card import format_cards_for_prompt

    ids = _card_ids(cards)
    parts = []
    for index, (card, pid) in enumerate(zip(cards, ids), 1):
        text = format_cards_for_prompt([card]).replace("### [1]", f"### [{index}]", 1)
        heading, _, body = text.partition("\n")
        parts.append(f"{heading}\n- Stable paper_id: {pid}\n{body}")
    return "\n\n".join(parts)


def _normalize_planner(planner: dict, cards: list[dict]) -> dict:
    """Map legacy one-based display indices to stable IDs and reject ambiguity."""
    ids = _card_ids(cards)
    known = set(ids)

    def resolve(value):
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise PlannerValidationError(f"Invalid planner reference ID: {value!r}")
        raw = str(value).strip()
        if raw in known:
            return raw
        index = re.fullmatch(r"(?:\[(\d+)\]|(\d+))", raw)
        if index:
            number = int(index.group(1) or index.group(2))
            if 1 <= number <= len(ids):
                return ids[number - 1]
        raise PlannerValidationError(f"Unknown planner reference ID: {raw!r}")

    if not isinstance(planner, dict):
        raise PlannerValidationError("Planner response must be a JSON object")
    title = planner.get("inferred_title")
    if not isinstance(title, str) or not title.strip():
        raise PlannerValidationError("Planner response is missing a specific title")
    included = planner.get("included_references")
    if not isinstance(included, list) or not included:
        raise PlannerValidationError("Planner selected no references")
    included_ids = [resolve(value) for value in included]
    if len(set(included_ids)) != len(included_ids):
        raise PlannerValidationError("Planner returned duplicate included reference IDs")

    raw_excluded = planner.get("excluded_references", [])
    if not isinstance(raw_excluded, list):
        raise PlannerValidationError("Planner exclusions must be a list")
    exclusions = []
    excluded_ids = set()
    for entry in raw_excluded:
        if not isinstance(entry, dict):
            raise PlannerValidationError("Planner exclusion must contain an ID and reason")
        pid = resolve(entry.get("id"))
        reason = entry.get("reason")
        if pid in excluded_ids or pid in included_ids:
            raise PlannerValidationError("Planner returned duplicate or conflicting reference IDs")
        if not isinstance(reason, str) or not reason.strip():
            raise PlannerValidationError("Planner exclusion is missing its reason")
        excluded_ids.add(pid)
        exclusions.append({"id": pid, "reason": reason.strip()})

    raw_roles = planner.get("citation_roles", {})
    if not isinstance(raw_roles, dict):
        raise PlannerValidationError("Planner citation roles must be an object")
    roles = {}
    for raw, role in raw_roles.items():
        pid = resolve(raw)
        if pid in roles or pid not in included_ids:
            raise PlannerValidationError(
                "Planner citation roles contain duplicate or unselected IDs"
            )
        roles[pid] = str(role)
    warnings = planner.get("warnings", [])
    warnings = list(warnings) if isinstance(warnings, list) else [str(warnings)]
    omitted = [pid for pid in ids if pid not in included_ids and pid not in excluded_ids]
    if omitted:
        warnings.append(f"Planner omitted exclusion rationale for {len(omitted)} references")
        exclusions.extend(
            {"id": pid, "reason": "Not selected by planner; no specific rationale provided"}
            for pid in omitted
        )
    return {
        **planner,
        "inferred_title": title.strip(),
        "included_references": included_ids,
        "excluded_references": exclusions,
        "citation_roles": roles,
        "warnings": warnings,
    }


def _raise_if_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise asyncio.CancelledError("Related Work generation was cancelled")


def _citation_usage(content: str, selected_cards: list[dict], planner: dict) -> str:
    """Count citations in the body, excluding the model's own usage checklist."""
    body = re.split(
        r"(?im)^\s*(?:#{1,6}\s*)?[*_]*(?:citation usage|references)[*_]*\s*:?[\*_]*\s*$",
        content,
        maxsplit=1,
    )[0]
    cited = set()
    for group in re.findall(r"\[([0-9,;\s–-]+)\]", body):
        for piece in re.split(r"[,;]", group):
            span = re.fullmatch(r"\s*(\d+)\s*[-–]\s*(\d+)\s*", piece)
            if span:
                start, end = map(int, span.groups())
                if 0 < start <= end <= len(selected_cards):
                    cited.update(range(start, end + 1))
            elif piece.strip().isdigit():
                cited.add(int(piece.strip()))
    lines = []
    for i, card in enumerate(selected_cards, 1):
        if i in cited:
            role = planner.get("citation_roles", {}).get(card["paper_id"], "cited in the body")
            lines.append(f"[{i}]: used — {role}")
        else:
            lines.append(f"[{i}]: not used — no citation found in the body")
    return "\n".join(lines)


# ── Planner ──────────────────────────────────────────────────────────────────

PLANNER_SYSTEM_PROMPT = """You are a senior researcher in embodied intelligence and robotics. You are planning a Related Work subsection for a robotics paper.

Your task: given a set of Reference Cards, produce a structured plan in JSON format.

## Input

You will receive:
1. Reference Cards — structured summaries of papers (method, key idea, contribution, limitation, suggested role).
2. Optional target template — the writing style to imitate.
3. Optional user instructions — additional constraints.

## Output — JSON only

Produce a single JSON object with these fields:

{
  "inferred_title": "string — a specific, technical subsection title. Never use vague titles like 'Related Work', 'Prior Work', 'Literature Review'. Must reflect the shared technical theme, e.g. 'Diffusion-Based Imitation Learning', 'Vision-Language-Action Models', 'Language-Guided Robotic Manipulation', '3D Perception for Robotic Manipulation', 'Data-Driven Policy Learning'.",
  "dominant_theme": "string — one-sentence summary of the unifying research direction.",
  "included_references": ["paper_id1", "paper_id2", ...],
  "excluded_references": [{"id": "paper_id", "reason": "why excluded"}],
  "narrative_plan": ["step 1", "step 2", "step 3", "step 4"],
  "citation_roles": {"paper_id": "role description"},
  "warnings": ["any concerns about coverage, missing information, or weak cards"]
}

## Selection rules

- If total cards ≤ 8: include ALL unless a card is clearly irrelevant to the dominant theme.
- If 9–15 cards: select 6–10 most relevant ones for a coherent subsection.
- If >15 cards: select the 6–10 most relevant under the dominant theme.
- Return the exact Stable paper_id values shown in the cards for included_references, excluded_references and citation_roles. The [1], [2], ... labels are display indices, not paper IDs.
- Every excluded reference MUST have a reason.
- citation_roles must describe each included reference's role in the paragraph, such as:
  foundational method, representative baseline, 3D extension, acceleration method,
  contrasting approach, application example, weakly related background.

## Title rules

- The title MUST be specific and technical.
- It should reflect the shared technique, problem domain, or approach of the included papers.
- BAD titles: "Related Work", "Prior Work", "Existing Studies", "Background", "Literature Review".
- GOOD titles: "Diffusion-Based Imitation Learning", "Vision-Language-Action Models for Manipulation", "Language-Guided Robotic Manipulation", "Tactile-Driven Policy Learning".

## Content planning rules

- narrative_plan should have 3–5 short steps describing the logical flow of the paragraph.
- Include: (1) opening framing, (2) grouping of methods, (3) comparison/contrast, (4) limitations, (5) positioning of current work.
- If current paper contribution is unknown, the final step should be a neutral gap statement.

Output ONLY the JSON object. No markdown fences, no extra text."""


def plan_related_work(
    cards: list[dict],
    instructions: str,
    template_content: str,
    task_topic: str,
    task_id: str,
) -> dict:
    """Run the Planner LLM to infer title, select references, and build narrative plan."""
    if not cards:
        raise PlannerValidationError("No Reference Cards available for planning")

    cards_text = _format_cards_full_for_planner(cards)
    user_prompt = f"""Task topic: {task_topic}

Reference Cards ({len(cards)} total):
{cards_text}"""
    if template_content:
        user_prompt += (
            f"\n\nTarget journal Related Work style example:\n---\n{template_content}\n---"
        )
    if instructions:
        user_prompt += f"\n\nAdditional instructions:\n{instructions}"
    user_prompt += "\n\nProduce the planner JSON now using the Stable paper_id values."

    agent = "Planner"
    entry = record_prompt(task_id, agent, PLANNER_SYSTEM_PROMPT, user_prompt, status="running")
    task_manager.broadcast(task_id, {"type": "prompt_log_entry", "entry": entry})
    start = time.time()
    content = ""
    try:
        response = get_llm().invoke(
            [SystemMessage(content=PLANNER_SYSTEM_PROMPT), HumanMessage(content=user_prompt)]
        )
        content = str(response.content or "").strip()
        planner = _normalize_planner(_parse_llm_json(content), cards)
    except Exception as error:
        failed = record_prompt(
            task_id,
            agent,
            "",
            "",
            f"{content}\n\nPlanner failed: {error}",
            int((time.time() - start) * 1000),
            "error",
            entry_id=entry["id"],
        )
        task_manager.broadcast(task_id, {"type": "prompt_log_entry", "entry": failed})
        raise PlannerValidationError(f"Related Work planning failed: {error}") from error
    completed = record_prompt(
        task_id,
        agent,
        "",
        "",
        content,
        int((time.time() - start) * 1000),
        "success",
        entry_id=entry["id"],
    )
    task_manager.broadcast(task_id, {"type": "prompt_log_entry", "entry": completed})
    return planner


# ── Final Writer ─────────────────────────────────────────────────────────────

FINAL_WRITER_SYSTEM_PROMPT = """You are a senior researcher in embodied intelligence, robot learning, and robotic manipulation. You write publication-ready Related Work paragraphs for top robotics venues.

## Your Task

Write exactly ONE Related Work subsection based on:
1. A planner result that specifies the subsection title, included references, and narrative plan.
2. Structured Reference Cards for the included references.
3. Optional target template for style imitation.
4. Optional user instructions.

## Output Format — MUST follow exactly

### <Use the planner's inferred_title EXACTLY as-is>

<One compact academic paragraph. No bullet points. No multiple subsections. No survey-style writing.>

Citation Usage:
- [1]: used — <role description>
- [2]: used — <role description>
- [3]: not used — <reason>
...

## Paragraph Requirements

- Write a SINGLE coherent paragraph, not multiple paragraphs.
- Do NOT use bullet points, numbered lists, or multi-section structure in the body.
- Follow the narrative plan provided by the planner.
- Synthesize papers into a logical narrative; do NOT list them one by one.
- Focus on: shared topic → representative works → technical comparison → limitations → current work positioning.
- Style: concise, formal, IEEE RA-L / ICRA / IROS tone.

## Citation Rules — CRITICAL

- ONLY use citation IDs [1], [2], ..., [N] that correspond to the selected Reference Cards provided below.
- The card index number IS the citation ID. Card [1] → cite as [1] in text.
- NEVER invent papers, authors, methods, datasets, experiments, or results.
- NEVER copy citation IDs from the target template unless those exact IDs also appear in the selected Reference Cards.
- Every factual claim about a paper MUST be supported by its Reference Card.
- If a card's information is marked "insufficient information", do not fabricate details.

## Style Imitation

- Imitate the provided template's: paragraph length, citation density, rhetorical structure, academic tone, transition style, level of technical detail.
- Do NOT copy template content verbatim.
- Do NOT use template's citation IDs.

## Citation Usage Section

- List EVERY selected reference card's citation ID.
- Mark each as "used" or "not used".
- For "used": describe its role in the paragraph (e.g., foundational method, representative baseline, contrasting approach).
- For "not used": explain why (e.g., "weakly related to dominant theme", "insufficient information in card").
- The used/not-used status MUST be consistent with actual citations in the paragraph.

## Current Work Positioning

- If user instructions describe the current paper's contribution, use it for the final positioning sentence.
- If not provided, end with a neutral gap statement. Do NOT fabricate a proposed method.

Output ONLY the subsection. Do not add explanations, outlines, or revision notes."""


def generate_related_work_from_cards(
    selected_cards: list[dict],
    planner: dict,
    instructions: str,
    template_content: str,
    task_topic: str,
    task_id: str,
    accumulated: dict,
) -> str:
    """Streaming final writer: generates the Related Work subsection from selected cards."""
    if not selected_cards:
        return ""

    inferred_title = planner.get("inferred_title", "Related Work")
    narrative_plan = planner.get("narrative_plan", [])
    plan_text = (
        "\n".join(f"  {i + 1}. {step}" for i, step in enumerate(narrative_plan))
        if narrative_plan
        else "  (no detailed plan)"
    )

    # Build compact card summary for writer
    card_entries = []
    for i, c in enumerate(selected_cards, 1):
        title = c.get("title", "?")[:120]
        authors = c.get("authors", "?")[:60]
        year = c.get("year", "?")
        problem = c.get("problem", "")[:150]
        method = c.get("method", "")[:120]
        key_idea = c.get("key_idea", "")[:150]
        contribution = c.get("main_contribution", "")[:150]
        strength = c.get("strength", "")[:120]
        limitation = c.get("limitation", "")[:120]
        role = c.get("suggested_related_work_role", "")

        entry = (
            f"### [{i}] {title}\n"
            f"- Authors: {authors} ({year})\n"
            f"- Problem: {problem}\n"
            f"- Method: {method}\n"
            f"- Key Idea: {key_idea}\n"
            f"- Contribution: {contribution}\n"
            f"- Strength: {strength}\n"
            f"- Limitation: {limitation}"
        )
        if role and role != "insufficient information":
            entry += f"\n- Suggested Role: {role}"
        card_entries.append(entry)

    cards_text = "\n\n".join(card_entries)

    user_prompt = f"""Task topic: {task_topic}

## Planner Result

Inferred title: {inferred_title}
Narrative plan:
{plan_text}

Citation roles:
{json.dumps(planner.get("citation_roles", {}), ensure_ascii=False)}

## Selected Reference Cards ({len(selected_cards)} papers)

{cards_text}"""

    if template_content:
        user_prompt += f"\n\n## Target Style Example (IMITATE STYLE ONLY — DO NOT COPY CITATION IDs)\n{template_content}"
    if instructions:
        user_prompt += f"\n\n## Additional Instructions\n{instructions}"

    user_prompt += (
        f"\n\nWrite the Related Work subsection now. Use the EXACT title: ### {inferred_title}"
    )

    return _stream_and_record(
        task_id,
        "FinalWriter",
        get_llm(),
        [
            SystemMessage(content=FINAL_WRITER_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ],
        accumulated,
    )


# ── streaming helper ─────────────────────────────────────────────────────────


def _stream_and_record(task_id: str, agent: str, llm, messages, accumulated: dict) -> str:
    """Stream LLM response, accumulate content, and record prompt log."""
    start = time.time()
    sys_msg = next((m.content for m in messages if isinstance(m, SystemMessage)), "")
    usr_msg = next((m.content for m in messages if isinstance(m, HumanMessage)), "")
    entry = record_prompt(task_id, agent, sys_msg, usr_msg, status="running")
    task_manager.broadcast(
        task_id,
        {
            "type": "prompt_log_entry",
            "entry": entry,
        },
    )

    full_content = ""
    try:
        _raise_if_cancelled(accumulated.get("cancel_event"))
        for chunk in llm.stream(messages):
            _raise_if_cancelled(accumulated.get("cancel_event"))
            content = chunk.content if hasattr(chunk, "content") else ""
            if content:
                full_content += content
                accumulated["content"] += content
                task_manager.broadcast(
                    task_id,
                    {
                        "type": "review_stream",
                        "review_id": accumulated.get("review_id", ""),
                        "content": content,
                    },
                )
    except (Exception, asyncio.CancelledError) as error:
        failed = record_prompt(
            task_id,
            agent,
            "",
            "",
            f"{full_content}\n\nGeneration incomplete: {error}",
            int((time.time() - start) * 1000),
            "error",
            entry_id=entry["id"],
        )
        task_manager.broadcast(task_id, {"type": "prompt_log_entry", "entry": failed})
        raise
    entry = record_prompt(
        task_id,
        agent,
        "",
        "",
        full_content,
        int((time.time() - start) * 1000),
        "success",
        entry_id=entry["id"],
    )
    task_manager.broadcast(task_id, {"type": "prompt_log_entry", "entry": entry})
    return full_content


# ── main entry point ─────────────────────────────────────────────────────────


async def write_review(
    task_id: str,
    paper_indices: list[int],
    instructions: str = "",
    template_content: str = "",
    *,
    review_id: str | None = None,
    cancel_event=None,
    task_snapshot=None,
    on_progress=None,
) -> str:
    """Generate a literature review via Planner → Final Writer pipeline. Returns review_id."""
    task = task_manager.get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    source_task = task_snapshot if task_snapshot is not None else task
    if source_task.id != task_id:
        raise ValueError("Review input snapshot belongs to a different task")

    def progress(stage, message, completed=0, total=1, **patch):
        if on_progress is not None:
            on_progress(
                {
                    "stage": stage,
                    "message": message,
                    "progress": {"completed": completed, "total": total},
                    **patch,
                }
            )

    _raise_if_cancelled(cancel_event)
    if review_id and not any(
        review.get("id") == review_id for review in (task.results or {}).get("custom_reviews", [])
    ):
        raise ValueError(f"Review {review_id} was not initialized")

    papers_with_idx = []
    for i in paper_indices:
        if 0 <= i < len(source_task.papers):
            papers_with_idx.append(
                (
                    i,
                    source_task.papers[i].model_dump()
                    if hasattr(source_task.papers[i], "model_dump")
                    else dict(source_task.papers[i]),
                )
            )
    papers = [p for _, p in papers_with_idx]
    valid_indices = [i for i, _ in papers_with_idx]

    if not papers:
        raise ValueError("No valid papers selected")
    progress("evidence", "准备论文资料与 Reference Cards", total=len(papers))

    # Ensure PDFs downloaded
    from backend.reference_card import _find_pdf_for_paper

    missing_indices = []
    for i, p in enumerate(papers):
        if _find_pdf_for_paper(task_id, p, source_task.keywords) is None:
            missing_indices.append(i)

    if missing_indices:
        progress("evidence", f"获取 {len(missing_indices)} 篇论文全文", total=len(papers))
        task_manager.broadcast(
            task_id,
            {
                "type": "review_started",
                "review_id": "",
                "paper_count": len(papers),
                "status_message": f"下载 {len(missing_indices)} 篇论文 PDF...",
            },
        )
        from backend.download_agent import download_papers as _download

        await asyncio.to_thread(
            _download, task_id, missing_indices, papers, source_task.year_from, source_task.keywords
        )

    _raise_if_cancelled(cancel_event)

    # Extract Reference Cards
    progress("evidence", "提取论文 Reference Cards", total=len(papers))
    task_manager.broadcast(
        task_id,
        {
            "type": "card_extract_progress",
            "current": 0,
            "total": len(papers),
            "status": "提取 Reference Cards...",
        },
    )

    cards = await asyncio.to_thread(
        extract_reference_cards,
        task_id,
        papers_with_idx,
        ", ".join(source_task.keywords),
        force_refresh=False,
    )

    _raise_if_cancelled(cancel_event)

    ordered_cards = []
    warnings = []
    for p in papers:
        p_hash = paper_hash(p)
        card = cards.get(p_hash)
        if card:
            ordered_cards.append(card)
            if card.get("confidence") == "low":
                warnings.append(f"Low confidence: {card.get('title', '')[:60]}")
        else:
            warnings.append(f"Card missing: {p.get('title', '')[:60]}")

    if not ordered_cards:
        raise ValueError("No Reference Cards available for selected papers")
    progress("evidence", "Reference Cards 已准备", len(ordered_cards), len(papers))

    review_id = review_id or uuid.uuid4().hex[:8]

    # Initialize review entry
    reviews = (task.results or {}).get("custom_reviews", [])
    existing_entry = next((r for r in reviews if r.get("id") == review_id), None)
    review_entry = existing_entry if existing_entry is not None else {}
    review_entry.update(
        {
            "id": review_id,
            "created_at": review_entry.get("created_at") or datetime.now().isoformat(),
            "paper_indices": valid_indices,
            "paper_titles": [p.get("title", "")[:80] for p in papers],
            "instructions": instructions,
            "template_id": review_entry.get("template_id", ""),
            "content": "",
            "status": "running",
            "inferred_title": "",
            "planner": {},
            "included_reference_ids": [],
            "excluded_references": [],
            "reference_cards": [c.get("paper_id") for c in ordered_cards],
            "card_cache_paths": [
                f"task_data/{task_id}/cards/{c.get('paper_id')}.json"
                for c in ordered_cards
                if c.get("paper_id")
            ],
            "citation_usage": "",
            "warnings": warnings,
        }
    )
    if existing_entry is None:
        reviews.append(review_entry)
    task_manager.update_task(task_id, results={**(task.results or {}), "custom_reviews": reviews})

    task_manager.broadcast(
        task_id,
        {
            "type": "review_started",
            "review_id": review_id,
            "paper_count": len(papers),
        },
    )

    acc = {"content": "", "review_id": review_id, "cancel_event": cancel_event}

    def _generate():
        try:
            _raise_if_cancelled(cancel_event)

            # ── Phase 1: Planner ──
            progress("planning", "正在规划 Related Work 主题与引用选择")
            _broadcast_stage(
                task_id, review_id, "planning", "正在规划 Related Work 主题与引用选择..."
            )

            planner = plan_related_work(
                ordered_cards,
                instructions,
                template_content,
                ", ".join(source_task.keywords),
                task_id,
            )

            _raise_if_cancelled(cancel_event)
            planner = _normalize_planner(planner, ordered_cards)
            by_id = {card["paper_id"]: card for card in ordered_cards}
            selected_cards = [by_id[pid] for pid in planner["included_references"]]
            warnings.extend(planner.get("warnings", []))
            review_entry["warnings"] = warnings

            # Update review entry with planner results
            review_entry["inferred_title"] = planner.get("inferred_title", "")
            review_entry["planner"] = {k: v for k, v in planner.items() if k != "warnings"}
            review_entry["included_reference_ids"] = [c.get("paper_id") for c in selected_cards]
            review_entry["excluded_references"] = planner.get("excluded_references", [])
            task_manager.update_task(
                task_id, results={**(task.results or {}), "custom_reviews": reviews}
            )
            progress("planning", f"已选定 {len(selected_cards)} 篇引用", 1, 1)

            _broadcast_stage(
                task_id,
                review_id,
                "planning_done",
                f"选定 {len(selected_cards)} 篇 / 标题: {planner.get('inferred_title', '')}",
            )
            task_manager.broadcast(
                task_id,
                {
                    "type": "prompt_log_entry",
                    "entry": record_prompt(
                        task_id,
                        "Planner-summary",
                        "",
                        "",
                        json.dumps(
                            {
                                k: planner.get(k)
                                for k in [
                                    "inferred_title",
                                    "included_references",
                                    "excluded_references",
                                ]
                            },
                            ensure_ascii=False,
                        ),
                        0,
                        "success",
                    ),
                },
            )

            # ── Phase 2: Final Writer ──
            progress("writing", "正在撰写 Related Work 正文")
            _broadcast_stage(task_id, review_id, "generating", "正在生成 Related Work 正文...")

            final_content = generate_related_work_from_cards(
                selected_cards,
                planner,
                instructions,
                template_content,
                ", ".join(source_task.keywords),
                task_id,
                acc,
            )

            _raise_if_cancelled(cancel_event)
            progress("auditing", "正在核对正文实际引用", content=final_content)
            citation_usage = _citation_usage(final_content, selected_cards, planner)

            review_entry["citation_usage"] = citation_usage
            task_manager.update_task(
                task_id, results={**(task.results or {}), "custom_reviews": reviews}
            )
            progress("auditing", "正文引用已核对", 1, 1, citation_usage=citation_usage)

        except Exception as error:
            # Failed planning or writing remains an explicit failure. Never
            # substitute unrelated first-N cards or concatenate another draft.
            acc["error"] = str(error)
            review_entry["error"] = str(error)
            task_manager.broadcast(
                task_id, {"type": "review_error", "review_id": review_id, "error": str(error)}
            )

    try:
        await asyncio.to_thread(_generate)
    except asyncio.CancelledError:
        # The job service owns cancellation status, but completed stream chunks
        # are still useful when the user resumes or inspects this attempt.
        reviews = (task.results or {}).get("custom_reviews", [])
        for review in reviews:
            if review.get("id") == review_id:
                review["content"] = acc.get("content", "")
                break
        task_manager.update_task(
            task_id, results={**(task.results or {}), "custom_reviews": reviews}
        )
        raise

    _raise_if_cancelled(cancel_event)

    # Save final content
    final_content = acc.get("content", "")
    reviews = (task.results or {}).get("custom_reviews", [])
    for r in reviews:
        if r["id"] == review_id:
            r["content"] = final_content
            r["status"] = "done" if final_content and not acc.get("error") else "failed"
            r["error"] = acc.get("error")
            r["updated_at"] = datetime.now().isoformat()
            break
    task_manager.update_task(task_id, results={**(task.results or {}), "custom_reviews": reviews})

    if acc.get("error") or not final_content:
        raise RuntimeError(acc.get("error") or "Review generation returned no content")

    task_manager.broadcast(
        task_id,
        {
            "type": "review_complete",
            "review_id": review_id,
            "content": final_content,
        },
    )

    return review_id


# ── Legacy generators retained for explicit callers; never automatic fallback ──


def _generate_single_batch(
    task_id: str,
    llm,
    cards: list[dict],
    instructions: str,
    template_content: str = "",
    accumulated: dict = None,
) -> str:
    """Legacy: single-batch generation using Reference Cards directly."""
    from backend.reference_card import format_cards_for_prompt

    cards_text = format_cards_for_prompt(cards)
    topic = cards[0].get("title", "")[:100] if cards else ""

    sys_prompt = """You are a senior researcher in embodied intelligence, robot learning, and robotic manipulation. You specialize in writing concise and publication-ready Related Work paragraphs for robotics papers.

Your task is to write one Related Work subsection based on structured Reference Cards provided below. Each card distills a paper into its problem, method, key idea, contribution, strengths, limitations, and suggested role.

## Core Task

Automatically infer the most suitable Related Work topic from the provided cards, then write one subsection title and one coherent Related Work paragraph.

The subsection title should summarize the shared research theme. It should be concise, academic, and consistent with robotics paper style, such as:
- Diffusion-Based Imitation Learning
- Language-Guided Robotic Manipulation
- Vision-Language-Action Models
- 3D Perception for Robotic Manipulation
- Policy Learning from Demonstrations
- Sim-to-Real Transfer for Robot Learning

Do not use vague titles such as "Related Work", "Prior Studies", or "Existing Methods".

## Writing Goal

Write a single Related Work subsection that matches the style of the provided example.

The output should usually include:
- one subsection title;
- one compact academic paragraph.

Do not write a survey-style review. Do not create multiple sections. Do not use bullet points. Do not over-explain background knowledge.

## Style Requirements

Imitate the provided Related Work example in paragraph length, citation density, rhetorical structure, academic tone, transition style, level of technical detail, way of introducing limitations, and way of motivating the current work.

Use a concise, formal, robotics-paper style similar to IEEE RA-L, ICRA, IROS, or T-RO papers.

## Citation Rules

Use only the papers described in the Reference Cards.
Do not invent papers, authors, years, citation numbers, methods, datasets, or experimental results.
Use the citation indices [1], [2], etc. as they appear in the cards.
Do not add references that were not provided.

## Content Rules

The paragraph should synthesize papers into a logical narrative, not list them one by one.
Focus on: shared topic → representative works → technical comparison → limitations → current work positioning.

## Output Format

Output in English unless the user explicitly asks for Chinese.

### <Automatically Inferred Subsection Title>

<One coherent Related Work paragraph>

Only output the final subsection. Do not explain your reasoning."""

    user_prompt = f"""Research topic context: {topic}...

Reference Cards ({len(cards)} papers in total):
{cards_text}"""

    if template_content:
        user_prompt += f"\n\nPlease refer to the writing style of the following target journal Related Works example:\n---\n{template_content}\n---"
    if instructions:
        user_prompt += f"\n\nAdditional instructions:\n{instructions}"

    return _stream_and_record(
        task_id,
        "综述Writer(legacy)",
        llm,
        [
            SystemMessage(content=sys_prompt),
            HumanMessage(content=user_prompt),
        ],
        accumulated or {"content": ""},
    )


def _generate_multi_batch(
    task_id: str,
    llm,
    cards: list[dict],
    instructions: str,
    template_content: str = "",
    accumulated: dict = None,
) -> str:
    """Legacy: multi-batch generation via batch summaries then synthesis."""
    from backend.reference_card import format_cards_for_prompt

    acc = accumulated or {"content": ""}
    batch_summaries = []
    for i in range(0, len(cards), BATCH_SIZE):
        batch = cards[i : i + BATCH_SIZE]
        topic = batch[0].get("title", "")[:100] if batch else ""
        cards_text = format_cards_for_prompt(batch)

        summary_prompt_sys = f"""You are a senior researcher in embodied intelligence and robot learning.
Read the following Reference Cards and generate a concise summary covering:
1. Core research themes of these papers
2. Key methods and contributions
3. Common limitations and open questions

Output in English, Markdown format. Output only the content, no extra explanations.

Research topic: {topic}...

Reference Cards ({len(batch)} papers):
{cards_text}"""

        batch_summary = _stream_and_record(
            task_id,
            f"综述Writer-分批摘要#{i // BATCH_SIZE + 1}",
            llm,
            [
                SystemMessage(content=summary_prompt_sys),
            ],
            acc,
        )
        batch_summaries.append(batch_summary)

    combined = "\n\n---\n\n".join(f"## Batch {i + 1}\n{s}" for i, s in enumerate(batch_summaries))

    sys_prompt = """You are a senior researcher in embodied intelligence, robot learning, and robotic manipulation. You specialize in writing concise and publication-ready Related Work paragraphs for robotics papers.

You have received batch summaries of multiple papers (derived from Reference Cards). Synthesize them into a coherent Related Work subsection.

## Output Requirements
- One subsection title summarizing the shared research theme
- One compact academic paragraph synthesizing the batch summaries
- Do not write a survey-style review. Do not create multiple sections. Do not use bullet points.

## Style Requirements
Imitate the provided Related Work example in paragraph length, citation density, rhetorical structure, academic tone, transition style, level of technical detail, and way of introducing limitations and motivating the current work.

## Citation Rules
Use only the papers from the Reference Cards. Do not invent papers, authors, years, or methods.

## Output Format
### <Automatically Inferred Subsection Title>

<One coherent Related Work paragraph>

Only output the final subsection."""

    user_prompt = f"""Batch summaries:

{combined}"""

    if template_content:
        user_prompt += f"\n\nPlease refer to the writing style of the following target journal Related Works example:\n---\n{template_content}\n---"
    if instructions:
        user_prompt += f"\n\nAdditional instructions:\n{instructions}"

    return _stream_and_record(
        task_id,
        "综述Writer-综合(legacy)",
        llm,
        [
            SystemMessage(content=sys_prompt),
            HumanMessage(content=user_prompt),
        ],
        acc,
    )

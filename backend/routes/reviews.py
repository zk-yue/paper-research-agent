from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from backend import template_store
from backend.schemas import (
    ExtractCardsRequest,
    WriteReviewRequest,
)
from backend.task_manager import task_manager

router = APIRouter(tags=["reviews"])


@router.get("/api/tasks/{task_id}/reference-cards")
async def get_reference_cards(task_id: str):
    """Get all cached Reference Cards for a task (UI summary format)."""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    from backend.reference_card import card_summary_for_ui, load_all_cards

    raw = load_all_cards(task_id)
    cards = {h: card_summary_for_ui(c) for h, c in raw.items()}
    return {"cards": cards}


@router.post("/api/tasks/{task_id}/reference-cards")
def extract_reference_cards(task_id: str, req: ExtractCardsRequest):
    """Extract Reference Cards for selected papers. Runs in thread pool (non-async)
    so the event loop stays free for WebSocket streaming."""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not req.paper_indices:
        raise HTTPException(status_code=400, detail="No papers selected")

    papers_with_idx = []
    invalid_indices = [i for i in req.paper_indices if i < 0 or i >= len(task.papers)]
    if invalid_indices:
        raise HTTPException(status_code=400, detail=f"Invalid paper indices: {invalid_indices}")

    for i in req.paper_indices:
        papers_with_idx.append(
            (
                i,
                task.papers[i].model_dump()
                if hasattr(task.papers[i], "model_dump")
                else dict(task.papers[i]),
            )
        )

    from backend.reference_card import extract_reference_cards as _extract_cards

    cards = _extract_cards(
        task_id,
        papers_with_idx,
        ", ".join(task.keywords),
        req.force_refresh,
    )

    task_manager.broadcast(
        task_id,
        {
            "type": "card_extract_complete",
            "card_count": len(cards),
        },
    )

    # Return UI-friendly summaries
    from backend.reference_card import card_summary_for_ui

    summaries = {h: card_summary_for_ui(c) for h, c in cards.items()}
    return {"cards": summaries}


@router.post("/api/tasks/{task_id}/write-review")
async def write_review(task_id: str, req: WriteReviewRequest):
    """Generate a literature review for selected papers."""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    invalid_indices = [i for i in req.paper_indices if i < 0 or i >= len(task.papers)]
    if invalid_indices:
        raise HTTPException(status_code=400, detail=f"Invalid paper indices: {invalid_indices}")

    # Load template content if specified
    template_content = ""
    if req.template_id:
        tpl = template_store.get_template(req.template_id)
        if tpl:
            papers = tpl.get("papers", [])
            if papers:
                parts = []
                for i, p in enumerate(papers):
                    title = p.get("title", "")
                    content = p.get("content", "")
                    if title:
                        parts.append(f"### 参考范文 {i + 1}：{title}\n{content}")
                    else:
                        parts.append(f"### 参考范文 {i + 1}\n{content}")
                template_content = "\n\n".join(parts)
            else:
                template_content = tpl.get("content", "")

    from backend.review_service import start_review

    try:
        review_id = start_review(
            task_id, req.paper_indices, req.instructions, template_content, req.mode, req.language
        )
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"review_id": review_id}


@router.post("/api/tasks/{task_id}/reviews/{review_id}/resume")
async def resume_review(task_id: str, review_id: str):
    from backend.review_service import resume_review as resume

    try:
        return {"review_id": resume(task_id, review_id)}
    except KeyError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, detail=str(exc)) from exc


@router.post("/api/tasks/{task_id}/reviews/{review_id}/cancel")
async def cancel_review(task_id: str, review_id: str):
    from backend.review_service import cancel_review as cancel

    try:
        return cancel(task_id, review_id)
    except KeyError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.get("/api/tasks/{task_id}/reviews/{review_id}/export")
async def export_review(task_id: str, review_id: str):
    from backend.review_service import get_review

    try:
        review = get_review(task_id, review_id)
    except KeyError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    if not review.get("content"):
        raise HTTPException(409, detail="尚无可导出的正文")
    return Response(
        review["content"],
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="review-{review_id}.md"'},
    )


@router.delete("/api/tasks/{task_id}/reviews/{review_id}")
async def delete_review(task_id: str, review_id: str):
    """Delete a specific review history entry from a task."""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    from backend.review_service import review_is_active

    if review_is_active(task_id, review_id):
        raise HTTPException(409, detail="请先停止正在生成的综述")
    reviews = (task.results or {}).get("custom_reviews", [])
    new_reviews = [r for r in reviews if r.get("id") != review_id]
    if len(new_reviews) == len(reviews):
        raise HTTPException(status_code=404, detail="Review not found")
    task_manager.update_task(
        task_id, results={**(task.results or {}), "custom_reviews": new_reviews}
    )
    return {"deleted": True}

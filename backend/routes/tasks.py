from fastapi import APIRouter, HTTPException

from backend.executor import execute_task
from backend.schemas import (
    CreateTask,
)
from backend.task_manager import task_manager

router = APIRouter(tags=["tasks"])


@router.post("/api/tasks")
async def create_task(req: CreateTask):
    keywords = req.keywords
    topic = (req.topic or "").strip() or None
    if req.search_mode == "deep":
        if not topic:
            raise HTTPException(status_code=400, detail="深度检索模式需要提供主题")
        if not keywords:
            keywords = [topic]
    task = task_manager.create_task(
        year_from=req.year_from,
        enable_literature=req.enable_literature,
        quick_mode=req.quick_mode,
        keywords=keywords,
        topic=topic,
        search_mode=req.search_mode,
        deep_rounds=req.deep_rounds,
    )
    # Start execution in background
    task_manager.start_task(task.id, execute_task(task.id))
    return {"task_id": task.id}


@router.get("/api/tasks")
async def list_tasks(summary: bool = False):
    return task_manager.list_summaries() if summary else task_manager.list_tasks()


@router.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    task = task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    task_manager.cancel_task(task_id)
    return task


@router.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    # Populate paper_id from URL if missing
    import re as _re

    for p in task.papers:
        pd = p.model_dump() if hasattr(p, "model_dump") else dict(p)
        if not pd.get("paper_id") and pd.get("url"):
            m = _re.search(r"arxiv\.org/abs/([^/?]+)", pd.get("url", ""))
            if m:
                p.paper_id = m.group(1)
    return task


@router.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    from backend.review_service import review_is_active

    if review_is_active(task_id):
        raise HTTPException(409, detail="请先停止此任务正在生成的综述")
    task = task_manager.get_task(task_id)
    year_from = task.year_from if task else None
    keywords = task.keywords if task else []
    # delete_task handles running tasks by cancelling them first
    deleted = task_manager.delete_task(task_id)
    if deleted and year_from is not None:
        from history import delete_history_by_task

        deleted_history = delete_history_by_task(keywords, year_from)
        return {"deleted": True, "history_cleaned": deleted_history}
    return {"deleted": deleted}


@router.post("/api/tasks/{task_id}/retry")
async def retry_task(task_id: str):
    """Create a new run while preserving the previous run and its artifacts."""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status in {"pending", "running"}:
        raise HTTPException(status_code=409, detail="任务正在运行，请先停止或等待完成")
    new_task = task_manager.create_task(
        year_from=task.year_from,
        enable_literature=task.enable_literature,
        quick_mode=task.quick_mode,
        keywords=task.keywords,
        topic=getattr(task, "topic", None),
        search_mode=getattr(task, "search_mode", "keyword"),
        deep_rounds=getattr(task, "deep_rounds", 3),
    )
    task_manager.start_task(new_task.id, execute_task(new_task.id))
    return {"task_id": new_task.id}

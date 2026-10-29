import asyncio

from fastapi import APIRouter, HTTPException

from backend.schemas import (
    DownloadHistoryRequest,
    DownloadPapersRequest,
)
from backend.task_manager import task_manager

router = APIRouter(tags=["papers"])


@router.get("/api/tasks/{task_id}/downloaded-papers")
async def list_downloaded_papers(task_id: str):
    """Return list of paper_ids that have been downloaded for this task."""
    task = task_manager.get_task(task_id)
    if not task:
        return {"paper_ids": []}

    from backend.task_paths import extract_title_from_pdf_filename
    from backend.task_paths import papers_dir as get_papers_dir

    papers_dir = get_papers_dir(task_id, task.keywords)
    if not papers_dir.is_dir():
        return {"paper_ids": []}

    filenames = sorted(p.name for p in papers_dir.glob("*.pdf"))
    downloaded_titles: set[str] = set()
    for fn in filenames:
        title = extract_title_from_pdf_filename(fn)
        if title:
            downloaded_titles.add(title)

    # Match against task papers
    downloaded_paper_ids: list[str] = []
    import re as _re

    for p in task.papers:
        pd = p.model_dump() if hasattr(p, "model_dump") else dict(p)
        title = pd.get("title", "")
        paper_id = pd.get("paper_id")
        # Extract paper_id from URL if missing
        if not paper_id and pd.get("url"):
            m = _re.search(r"arxiv\.org/abs/([^/?]+)", pd.get("url", ""))
            if m:
                paper_id = m.group(1)
        # Normalize: sanitize removes \\ / : * ? " < > | — strip those chars for comparison
        normalized = _re.sub(r'[\\/:*?"<>|]', "", title).strip()
        # Exact match or prefix match
        for dt in downloaded_titles:
            if normalized == dt or normalized.startswith(dt) or dt.startswith(normalized):
                if paper_id:
                    downloaded_paper_ids.append(paper_id)
                break

    return {"paper_ids": downloaded_paper_ids}


@router.post("/api/tasks/{task_id}/download-papers")
async def download_papers(task_id: str, req: DownloadPapersRequest):
    """Download selected papers from a task's paper list."""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Get papers from task data (may be in-memory or on disk)
    papers = []
    for p in task.papers:
        pd = p.model_dump() if hasattr(p, "model_dump") else dict(p)
        # Extract paper_id from URL if missing
        if not pd.get("paper_id") and pd.get("url"):
            import re

            m = re.search(r"arxiv\.org/abs/([^/?]+)", pd.get("url", ""))
            if m:
                pd["paper_id"] = m.group(1)
        papers.append(pd)

    invalid_indices = [i for i in req.paper_indices if i < 0 or i >= len(papers)]
    if invalid_indices:
        raise HTTPException(status_code=400, detail=f"Invalid paper indices: {invalid_indices}")

    from backend.download_agent import download_papers as _download

    results = await asyncio.to_thread(
        _download, task_id, req.paper_indices, papers, task.year_from, task.keywords
    )
    return {"results": results}


@router.post("/api/download-papers-from-history")
async def download_papers_from_history(req: DownloadHistoryRequest):
    """Download papers from completed tasks (loaded from disk)."""
    from backend.download_agent import download_papers_from_history as _download_history

    results = await asyncio.to_thread(_download_history, req.task_ids)
    return {"results": results}

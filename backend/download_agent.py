"""论文下载 Agent — 按选中论文列表批量下载 PDF。"""

import logging
import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from backend.task_manager import task_manager
from backend.task_paths import (
    ensure_task_dir,
    load_keywords_from_legacy_json,
    resolve_metadata_file,
    slugify_keywords,
)
from backend.task_paths import (
    papers_dir as get_papers_dir,
)


def sanitize_filename(title: str, max_len: int = 80) -> str:
    """Remove unsafe chars and truncate."""
    title = re.sub(r'[\\/:*?"<>|]', "", title).strip()
    if len(title) > max_len:
        title = title[:max_len].rsplit(" ", 1)[0]
    return title


def _resolve_paper_year(paper: dict, fallback: int) -> str:
    """Publication year from paper metadata, or fallback when unknown."""
    if paper.get("year"):
        return str(paper["year"])[:4]
    if paper.get("published_date"):
        return str(paper["published_date"])[:4]
    return str(fallback)


def build_paper_filename(paper: dict, title: str, year_fallback: int) -> str:
    """PDF filename: {publication_year}_{title}.pdf (theme lives in parent task folder)."""
    year = _resolve_paper_year(paper, year_fallback)
    safe_title = sanitize_filename(title)
    return f"{year}_{safe_title}.pdf"


def _legacy_paper_filenames(title: str, keywords: list[str], year_fallback: int) -> list[str]:
    """Older filename patterns (search year_from, optionally with theme prefix)."""
    safe_title = sanitize_filename(title)
    yf = str(year_fallback)
    theme = slugify_keywords(keywords, max_len=35)
    return [
        f"{yf}_{safe_title}.pdf",
        f"{theme}_{yf}_{safe_title}.pdf",
    ]


def extract_arxiv_id(paper: dict) -> str | None:
    """Extract arXiv ID from paper dict (supports both new and legacy records)."""
    paper_id = str(paper.get("paper_id") or "").strip()
    m = re.match(r"^(?:arxiv:)?(\d{4}\.\d{4,5}(v\d+)?)$", paper_id, re.IGNORECASE)
    if m:
        return m.group(1)
    url = paper.get("url", "") or paper.get("pdf_url", "") or ""
    m = re.search(r"arxiv\.org/(abs|pdf)/(\d{4}\.\d{4,5}(v\d+)?)", url)
    if m:
        return m.group(2)
    return None


def download_single_pdf(pdf_url: str, save_path: str) -> bool:
    """Download a single PDF file. Returns True on success."""
    temporary = None
    try:
        destination = Path(save_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(pdf_url, timeout=120, stream=True) as response:
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, suffix=".part", delete=False
            ) as stream:
                temporary = Path(stream.name)
                for chunk in response.iter_content(chunk_size=8192):
                    stream.write(chunk)
        with temporary.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise ValueError("Download did not return a PDF")
        os.replace(temporary, destination)
        return True
    except (requests.RequestException, OSError, ValueError):
        logging.getLogger(__name__).warning("PDF download failed: %s", pdf_url, exc_info=True)
        return False
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


MAX_WORKERS = 5


def download_papers(
    task_id: str,
    paper_indices: list[int],
    papers: list[dict],
    year_from: int,
    keywords: list[str] | None = None,
) -> dict:
    """
    Download selected papers to task_data/{theme}__{task_id}/papers/.
    File format: {publication_year}_{title}.pdf

    Returns a dict of {paper_idx: {success: bool, path: str, error: str}}
    """
    task = task_manager.get_task(task_id)
    kw = keywords if keywords is not None else (task.keywords if task else [])
    if not kw:
        kw = load_keywords_from_legacy_json(task_id)

    ensure_task_dir(task_id, kw)
    download_dir = get_papers_dir(task_id, kw)

    results = {}
    total = len(paper_indices)
    completed_count = [0]
    count_lock = threading.Lock()

    def _advance_and_broadcast():
        with count_lock:
            completed_count[0] += 1
            done = completed_count[0]
        task_manager.broadcast(
            task_id,
            {
                "type": "download_progress",
                "paper_idx": done,
                "total": total,
                "status": f"已下载 {done}/{total} 篇",
            },
        )

    def _download_one(idx: int) -> None:
        """Download a single paper. Skips if file already exists."""
        if idx < 0 or idx >= len(papers):
            results[idx] = {"success": False, "error": "索引超出范围"}
            _advance_and_broadcast()
            return
        paper = papers[idx]
        title = paper.get("title", f"untitled_{idx}")
        filename = build_paper_filename(paper, title, year_from)
        save_path = download_dir / filename
        legacy_paths = [
            download_dir / name for name in _legacy_paper_filenames(title, kw, year_from)
        ]

        # Skip if already downloaded (current or legacy filename)
        existing = next(
            (p for p in [save_path, *legacy_paths] if p.exists()),
            None,
        )
        if existing:
            results[idx] = {"success": True, "path": str(existing)}
        else:
            arxiv_id = extract_arxiv_id(paper)
            if arxiv_id:
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            elif paper.get("pdf_url"):
                pdf_url = paper["pdf_url"]
            else:
                results[idx] = {"success": False, "error": "未找到 PDF 链接"}
                _advance_and_broadcast()
                return
            success = download_single_pdf(pdf_url, str(save_path))
            if success:
                results[idx] = {"success": True, "path": str(save_path)}
            else:
                results[idx] = {"success": False, "error": "下载失败"}
        _advance_and_broadcast()

    # Download in parallel
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_download_one, idx): idx for idx in paper_indices}
        for future in as_completed(futures):
            future.result()

    # Final progress
    downloaded = sum(1 for r in results.values() if r.get("success"))
    task_manager.broadcast(
        task_id,
        {
            "type": "download_progress",
            "paper_idx": total,
            "total": total,
            "status": f"下载完成: {downloaded}/{total} 篇",
        },
    )

    return results


def download_papers_from_history(
    task_ids: list[str],
) -> dict:
    """
    Download papers from completed tasks (read from disk).
    Returns {task_id: {paper_idx: {success, path, error}}}.
    """
    import json

    all_results = {}

    for task_id in task_ids:
        task_file = resolve_metadata_file(task_id)
        if not task_file:
            all_results[task_id] = {"error": f"任务 {task_id} 不存在"}
            continue

        with open(task_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        papers = data.get("papers", [])
        if not papers:
            all_results[task_id] = {"error": "无论文数据"}
            continue

        # Convert to dict format with paper_id from URL
        paper_dicts = []
        for p in papers:
            pd = dict(p)
            if not pd.get("paper_id") and pd.get("url"):
                m = re.search(r"arxiv\.org/abs/([^/?]+)", pd.get("url", ""))
                if m:
                    pd["paper_id"] = m.group(1)
            paper_dicts.append(pd)

        indices = list(range(len(paper_dicts)))
        year_from = data.get("year_from", 2025)
        keywords = data.get("keywords", [])

        results = download_papers(task_id, indices, paper_dicts, year_from, keywords)
        all_results[task_id] = results

    return all_results

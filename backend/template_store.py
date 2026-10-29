"""Review template storage — CRUD for journal-related-works templates.

Each template holds a list of papers (title + content) for style reference.
"""

import json
import logging
import uuid
from datetime import datetime
from functools import wraps
from threading import RLock

from settings import DATA_ROOT
from storage import atomic_write_json, json_path

TEMPLATES_DIR = DATA_ROOT / "review_templates"
_lock = RLock()
logger = logging.getLogger(__name__)


def synchronized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _lock:
            return function(*args, **kwargs)

    return wrapped


def list_templates() -> list[dict]:
    """Return summary of all templates."""
    templates = []
    for f in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not {"id", "created_at", "journal_name"}.issubset(
                data
            ):
                raise ValueError("Invalid stored record")
        except (ValueError, OSError):
            logger.warning("Skipping unreadable record: %s", f, exc_info=True)
            continue
        papers = data.get("papers", [])
        preview = ""
        if papers:
            preview = papers[0].get("content", "")[:100]
        else:
            preview = data.get("content", "")[:100]  # backward compat
        templates.append(
            {
                "id": data["id"],
                "journal_name": data["journal_name"],
                "created_at": data["created_at"],
                "content_preview": preview,
                "paper_count": len(papers),
            }
        )
    return templates


@synchronized
def create_template(journal_name: str, papers: list[dict]) -> dict:
    """Create a new template. papers = [{title, content}, ...]."""
    template_id = uuid.uuid4().hex[:8]
    template = {
        "id": template_id,
        "journal_name": journal_name.strip(),
        "papers": papers,
        "created_at": datetime.now().isoformat(),
    }
    _save(template)
    return template


def get_template(template_id: str) -> dict | None:
    """Get a single template by ID."""
    path = json_path(TEMPLATES_DIR, template_id)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@synchronized
def update_template(
    template_id: str, journal_name: str | None = None, papers: list[dict] | None = None
) -> dict | None:
    """Update an existing template."""
    path = json_path(TEMPLATES_DIR, template_id)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if journal_name is not None:
        data["journal_name"] = journal_name.strip()
    if papers is not None:
        data["papers"] = papers
    # Migrate legacy single-content field to papers list
    if "content" in data and not data.get("papers"):
        data["papers"] = [{"title": "", "content": data.pop("content")}]
    elif "content" in data:
        del data["content"]
    _save(data)
    return data


@synchronized
def delete_template(template_id: str) -> bool:
    """Delete a template. Returns True if deleted."""
    path = json_path(TEMPLATES_DIR, template_id)
    if path.exists():
        path.unlink()
        return True
    return False


def _save(data: dict):
    atomic_write_json(json_path(TEMPLATES_DIR, data["id"]), data)

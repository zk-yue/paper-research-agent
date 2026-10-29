"""Instruction preset storage — CRUD for reusable review prompts."""

import json
import logging
import uuid
from datetime import datetime
from functools import wraps
from threading import RLock

from settings import DATA_ROOT
from storage import atomic_write_json, json_path

PRESETS_DIR = DATA_ROOT / "instruction_presets"
_lock = RLock()
logger = logging.getLogger(__name__)


def synchronized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _lock:
            return function(*args, **kwargs)

    return wrapped


def list_presets() -> list[dict]:
    """Return summary of all presets."""
    presets = []
    for f in sorted(PRESETS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not {"id", "created_at", "label"}.issubset(data):
                raise ValueError("Invalid stored record")
        except (ValueError, OSError):
            logger.warning("Skipping unreadable record: %s", f, exc_info=True)
            continue
        content = data.get("content", "")
        presets.append(
            {
                "id": data["id"],
                "label": data["label"],
                "content": content,
                "content_preview": content[:80],
                "created_at": data["created_at"],
            }
        )
    return presets


@synchronized
def create_preset(label: str, content: str) -> dict:
    """Create a new preset. Returns the preset dict."""
    preset_id = uuid.uuid4().hex[:8]
    preset = {
        "id": preset_id,
        "label": label.strip(),
        "content": content,
        "created_at": datetime.now().isoformat(),
    }
    _save(preset)
    return preset


def get_preset(preset_id: str) -> dict | None:
    """Get a single preset by ID."""
    path = json_path(PRESETS_DIR, preset_id)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@synchronized
def update_preset(
    preset_id: str, label: str | None = None, content: str | None = None
) -> dict | None:
    """Update an existing preset."""
    path = json_path(PRESETS_DIR, preset_id)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if label is not None:
        data["label"] = label.strip()
    if content is not None:
        data["content"] = content
    _save(data)
    return data


@synchronized
def delete_preset(preset_id: str) -> bool:
    """Delete a preset. Returns True if deleted."""
    path = json_path(PRESETS_DIR, preset_id)
    if path.exists():
        path.unlink()
        return True
    return False


def _save(data: dict):
    atomic_write_json(json_path(PRESETS_DIR, data["id"]), data)

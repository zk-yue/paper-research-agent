"""Prompt logging — tracks every LLM call with agent name and prompt content."""

import json
import logging
import os
import time
import uuid
from functools import wraps
from threading import RLock

from settings import DATA_ROOT
from storage import atomic_write_json, json_path

LOG_DIR = DATA_ROOT / "backend" / "data" / "prompt_logs"
logger = logging.getLogger(__name__)
_lock = RLock()


def synchronized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _lock:
            return function(*args, **kwargs)

    return wrapped


# In-memory store: task_id -> list of log entries
_store: dict[str, list[dict]] = {}


def _log_path(task_id: str) -> str:
    return json_path(LOG_DIR, task_id)


def _save_to_disk(task_id: str):
    try:
        atomic_write_json(_log_path(task_id), _store.get(task_id, []))
    except OSError:
        logger.warning("Cannot persist prompt log for %s", task_id, exc_info=True)


def _load_from_disk(task_id: str) -> list[dict]:
    path = _log_path(task_id)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                entries = json.load(f)
                if not isinstance(entries, list) or any(
                    not isinstance(entry, dict) for entry in entries
                ):
                    raise ValueError("Prompt log must contain an array of records")
                return entries
        except (OSError, ValueError):
            logger.warning("Cannot read prompt log for %s", task_id, exc_info=True)
            return []
    return []


@synchronized
def record_prompt(
    task_id: str,
    agent: str,
    system_prompt: str,
    user_prompt: str,
    response_preview: str = "",
    duration_ms: int = 0,
    status: str = "running",
    entry_id: str | None = None,
) -> dict:
    """Record an LLM call. Returns the log entry."""
    if task_id not in _store:
        _store[task_id] = _load_from_disk(task_id)

    # If updating an existing running entry for this agent
    if status != "running":
        for entry in reversed(_store[task_id]):
            if (entry["id"] == entry_id if entry_id else entry["agent"] == agent) and entry[
                "status"
            ] == "running":
                entry["response_preview"] = response_preview if response_preview else ""
                entry["duration_ms"] = duration_ms
                entry["status"] = status
                _save_to_disk(task_id)
                return dict(entry)

    entry = {
        "id": uuid.uuid4().hex[:8],
        "agent": agent,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "response_preview": response_preview if response_preview else "",
        "duration_ms": duration_ms,
        "status": status,
        "timestamp": time.time(),
    }
    _store[task_id].append(entry)
    _save_to_disk(task_id)
    return dict(entry)


@synchronized
def get_log(task_id: str) -> list[dict]:
    """Get prompt log for a task."""
    if task_id not in _store:
        _store[task_id] = _load_from_disk(task_id)
    return [dict(entry) for entry in _store.get(task_id, [])]


@synchronized
def delete_entry(task_id: str, entry_id: str) -> bool:
    """Delete a single prompt log entry by id."""
    if task_id not in _store:
        _store[task_id] = _load_from_disk(task_id)
    entries = _store.get(task_id, [])
    new_entries = [e for e in entries if e["id"] != entry_id]
    if len(new_entries) == len(entries):
        return False
    _store[task_id] = new_entries
    _save_to_disk(task_id)
    return True


@synchronized
def clear_log(task_id: str):
    """Clear prompt log for a task."""
    _store.pop(task_id, None)
    path = _log_path(task_id)
    if os.path.exists(path):
        os.remove(path)

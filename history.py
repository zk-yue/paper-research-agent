"""历史记录管理模块"""

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from settings import DATA_ROOT
from storage import atomic_write_json, json_path

HISTORY_DIR = DATA_ROOT / "history"


def ensure_history_dir():
    """Ensure history directory exists."""
    os.makedirs(HISTORY_DIR, exist_ok=True)


def save_history(
    keywords: list[str],
    year_from: int,
    literature_review: str,
    papers_display: str,
    execution_log: str,
    *,
    research_details: Optional[Dict] = None,
) -> str:
    """Save a conversation to history and return the history ID."""
    ensure_history_dir()

    history_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
    history_entry = {
        "id": history_id,
        "timestamp": datetime.now().isoformat(),
        "keywords": keywords,
        "year_from": year_from,
        "literature_review": literature_review,
        "papers_display": papers_display,
        "execution_log": execution_log,
    }

    if research_details is not None:
        history_entry["research_details"] = research_details
    filepath = json_path(Path(HISTORY_DIR), history_id)
    atomic_write_json(filepath, history_entry)

    return history_id


def load_history_list() -> List[Dict]:
    """Load all history entries (summary only)."""
    ensure_history_dir()

    history_list = []
    for filename in sorted(os.listdir(HISTORY_DIR), reverse=True):
        if filename.endswith(".json"):
            filepath = os.path.join(HISTORY_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    entry = json.load(f)
                    history_list.append(
                        {
                            "id": entry.get("id", ""),
                            "timestamp": entry.get("timestamp", ""),
                            "keywords": entry.get("keywords", []),
                            "year_from": entry.get("year_from", 2025),
                        }
                    )
            except (json.JSONDecodeError, IOError):
                continue

    return history_list


def load_history_detail(history_id: str) -> Optional[Dict]:
    """Load a specific history entry by ID."""
    ensure_history_dir()

    filepath = json_path(Path(HISTORY_DIR), history_id)
    if not os.path.exists(filepath):
        return None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def delete_history(history_id: str) -> bool:
    """Delete a specific history entry by ID."""
    ensure_history_dir()

    filepath = json_path(Path(HISTORY_DIR), history_id)
    if os.path.exists(filepath):
        os.remove(filepath)
        return True
    return False


def delete_history_by_task(keywords: list[str], year_from: int) -> int:
    """Delete history entries matching a task's keywords and year_from. Returns count deleted."""
    ensure_history_dir()
    count = 0
    for filename in os.listdir(HISTORY_DIR):
        if not filename.endswith(".json"):
            continue
        filepath = os.path.join(HISTORY_DIR, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                entry = json.load(f)
            if entry.get("keywords") == keywords and entry.get("year_from") == year_from:
                os.remove(filepath)
                count += 1
        except (json.JSONDecodeError, IOError, OSError):
            continue
    return count


def clear_all_history() -> int:
    """Clear all history entries and return the count of deleted files."""
    ensure_history_dir()

    count = 0
    for filename in os.listdir(HISTORY_DIR):
        if filename.endswith(".json"):
            os.remove(os.path.join(HISTORY_DIR, filename))
            count += 1

    return count


def format_history_list_for_display(history_list: List[Dict]) -> str:
    """Format history list for display in Web UI."""
    if not history_list:
        return "暂无历史记录"

    lines = ["## 历史记录\n"]
    for i, entry in enumerate(history_list, 1):
        timestamp = entry.get("timestamp", "")
        keywords = entry.get("keywords", [])
        year_from = entry.get("year_from", 2025)

        if timestamp:
            try:
                dt = datetime.fromisoformat(timestamp)
                time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                time_str = timestamp
        else:
            time_str = "未知时间"

        display_name = ", ".join(keywords) if keywords else "未知主题"
        lines.append(f"### {i}. {display_name}")
        lines.append(f"- **时间**: {time_str}")
        lines.append(f"- **年份范围**: {year_from} 年至今")
        lines.append(f"- **ID**: `{entry.get('id', '')}`")
        lines.append("")

    return "\n".join(lines)

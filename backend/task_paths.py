"""Per-task directory layout under task_data/."""

import json
import re
from pathlib import Path

from settings import DATA_ROOT
from storage import contained_path, json_path, validate_identifier

TASKS_DIR = DATA_ROOT / "task_data"
TASK_ID_SEP = "__"


def slugify_keywords(keywords: list[str], max_len: int = 40) -> str:
    """Build a filesystem-safe theme slug from search keywords."""
    text = "_".join(k.strip() for k in keywords if k and k.strip())
    if not text:
        return "untitled"
    text = re.sub(r'[\\/:*?"<>|]', "", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if len(text) > max_len:
        text = text[:max_len].rstrip("_")
    return text or "untitled"


def build_task_folder_name(task_id: str, keywords: list[str]) -> str:
    """task_data/{theme}__{task_id}/"""
    return f"{slugify_keywords(keywords)}{TASK_ID_SEP}{validate_identifier(task_id)}"


def parse_task_id_from_folder(folder_name: str) -> str:
    if TASK_ID_SEP in folder_name:
        return folder_name.rsplit(TASK_ID_SEP, 1)[-1]
    return folder_name


def load_keywords_from_legacy_json(task_id: str) -> list[str]:
    legacy = legacy_metadata_file(task_id)
    if not legacy.is_file():
        return []
    try:
        data = json.loads(legacy.read_text(encoding="utf-8"))
        return (data.get("keywords") or []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError):
        return []


def _merge_subdir(src: Path, dst: Path) -> None:
    if not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            _merge_subdir(item, target)
        elif not target.exists():
            item.rename(target)


def find_task_dir(task_id: str) -> Path | None:
    """Locate an existing task directory by task id (themed name preferred)."""
    validate_identifier(task_id)
    if not TASKS_DIR.is_dir():
        return None

    suffix = f"{TASK_ID_SEP}{task_id}"
    themed = [
        p
        for p in TASKS_DIR.iterdir()
        if p.is_dir() and not p.is_symlink() and p.name.endswith(suffix)
    ]
    if themed:
        return sorted(themed, key=lambda p: p.stat().st_mtime, reverse=True)[0]

    plain = contained_path(TASKS_DIR, task_id)
    if plain.is_dir():
        return plain
    return None


def get_task_root(task_id: str, keywords: list[str] | None = None) -> Path:
    """Resolve task root path; use themed name when creating a new folder."""
    found = find_task_dir(task_id)
    if found:
        return found
    kw = keywords or load_keywords_from_legacy_json(task_id)
    if kw:
        return contained_path(TASKS_DIR, build_task_folder_name(task_id, kw))
    return contained_path(TASKS_DIR, task_id)


def task_metadata_file(task_id: str, keywords: list[str] | None = None) -> Path:
    return get_task_root(task_id, keywords) / "task.json"


def legacy_metadata_file(task_id: str) -> Path:
    return json_path(TASKS_DIR, task_id)


def papers_dir(task_id: str, keywords: list[str] | None = None) -> Path:
    return get_task_root(task_id, keywords) / "papers"


def cards_dir(task_id: str, keywords: list[str] | None = None) -> Path:
    return get_task_root(task_id, keywords) / "cards"


def ensure_task_dir(task_id: str, keywords: list[str] | None = None) -> Path:
    """Create or migrate task folder to themed layout with papers/ subfolder."""
    validate_identifier(task_id)
    kw = keywords or load_keywords_from_legacy_json(task_id)
    target = contained_path(TASKS_DIR, build_task_folder_name(task_id, kw) if kw else task_id)

    existing = find_task_dir(task_id)
    if existing and existing != target:
        if not target.exists():
            existing.rename(target)
        else:
            # Keep the source when names collide; migration must never discard
            # PDFs, cached cards or unknown files belonging to an older version.
            _merge_subdir(existing, target)

    target.mkdir(parents=True, exist_ok=True)
    (target / "papers").mkdir(parents=True, exist_ok=True)

    legacy_json = legacy_metadata_file(task_id)
    task_json = target / "task.json"
    if legacy_json.is_file():
        if not task_json.is_file():
            legacy_json.rename(task_json)
        else:
            legacy_json.unlink()

    return target


def resolve_metadata_file(task_id: str) -> Path | None:
    """Return task metadata path (themed dir, plain dir, then legacy flat file)."""
    root = find_task_dir(task_id)
    if root:
        meta = root / "task.json"
        if meta.is_file():
            return meta
    legacy_path = legacy_metadata_file(task_id)
    if legacy_path.is_file():
        return legacy_path
    return None


def iter_metadata_files() -> list[Path]:
    """List all task metadata files without duplicate task ids."""
    seen_ids: set[str] = set()
    files: list[Path] = []

    if not TASKS_DIR.is_dir():
        return files

    for entry in TASKS_DIR.iterdir():
        if not entry.is_dir() or entry.is_symlink():
            continue
        meta = entry / "task.json"
        if not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            tid = data.get("id") if isinstance(data, dict) else None
            if not isinstance(tid, str) or not tid:
                tid = parse_task_id_from_folder(entry.name)
        except (json.JSONDecodeError, OSError):
            tid = parse_task_id_from_folder(entry.name)
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        files.append(meta)

    for meta in TASKS_DIR.glob("*.json"):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            tid = data.get("id") if isinstance(data, dict) else None
            if not isinstance(tid, str) or not tid:
                tid = meta.stem
        except (json.JSONDecodeError, OSError):
            tid = meta.stem
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        files.append(meta)

    return files


def extract_title_from_pdf_filename(filename: str) -> str | None:
    """Extract sanitized title from PDF filename (supports themed and legacy names)."""
    stem = Path(filename).stem
    m = re.search(r"_(\d{4})_(.+)$", stem)
    if m:
        return m.group(2)
    m = re.match(r"^(\d{4})_(.+)$", stem)
    if m:
        return m.group(2)
    return None

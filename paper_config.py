"""Local paper-library settings shared by the optional CLI utilities.

The mutable library lives under DATA_ROOT and is never shipped as package data.
A fresh installation starts empty; an unreadable existing library is not reset.
"""

import json
from pathlib import Path

from settings import DATA_ROOT
from storage import atomic_write_json

CONFIG_PATH = DATA_ROOT / "config" / "papers_keywords.json"


def empty_config() -> dict:
    return {
        "papers": [],
        "keywords": [],
        "description": "Personal paper library and search keywords",
    }


def load_config(path: Path | None = None) -> dict:
    target = Path(path) if path is not None else CONFIG_PATH
    if not target.exists():
        return empty_config()
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Cannot read paper configuration at {target}; existing data was kept."
        ) from error
    if not isinstance(config, dict) or not all(
        isinstance(config.get(key), list) for key in ("papers", "keywords")
    ):
        raise ValueError(
            f"Invalid paper configuration at {target}: papers and keywords must be lists."
        )
    return config


def save_config(config: dict, path: Path | None = None) -> None:
    atomic_write_json(Path(path) if path is not None else CONFIG_PATH, config)

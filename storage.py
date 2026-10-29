"""Small filesystem primitives shared by the JSON stores.

Writes are atomic for readers. Callers still need a lock around read/modify/write
transactions; the application supports a single backend worker.
"""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


class InvalidIdentifier(ValueError):
    """An external identifier cannot safely be used as a filename."""


def validate_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise InvalidIdentifier("Invalid resource identifier.")
    return value


def contained_path(root: Path, *parts: str) -> Path:
    root = root.resolve()
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root):
        raise InvalidIdentifier("Resource path is outside its storage directory.")
    return path


def json_path(root: Path, identifier: str) -> Path:
    return contained_path(root, f"{validate_identifier(identifier)}.json")


def atomic_write_json(path: Path, data: Any) -> None:
    """Replace a UTF-8 JSON file only after serialization and writing succeed."""
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

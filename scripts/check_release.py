#!/usr/bin/env python3
"""Offline, redacted checks for source publication boundaries.

The default checks tracked working files plus non-ignored untracked files.
``--staged`` checks the *entire index*, including staged content that differs
from disk. ``--history`` audits all reachable Git history without rewriting it.
``--archive`` inspects source archives, sdists and wheels without extracting them.

Findings contain paths, categories and line numbers, never matched values.
This is a publication guard, not proof that arbitrary user content is public.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000

PRIVATE_DIRS = {
    ".git",
    ".codex",
    ".claude",
    ".omc",
    ".ssh",
    ".aws",
    ".azure",
    "task_data",
    "history",
    "outputs",
    "runs",
    "instruction_presets",
    "review_templates",
    "prompt_logs",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "secrets",
}
AUTH_NAMES = {
    "auth.json",
    "credentials.json",
    ".netrc",
    "_netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    ".gitconfig",
    ".bash_history",
    ".zsh_history",
    ".python_history",
    "cookies.json",
    "storage_state.json",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
}
RUNTIME_NAMES = {".ds_store", ".coverage", "coverage.xml"}
PRIVATE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3", ".log"}
SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    ),
    ("openai_key", re.compile(r"\bsk-(?:(?:proj|svcacct)-)?[A-Za-z0-9_-]{24,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    (
        "jwt_token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{16,}\b"),
    ),
    (
        "credential_assignment",
        re.compile(
            r"""(?im)(?:["']?)(?:[A-Z0-9_]{0,64}(?:API_KEY|ACCESS_TOKEN|REFRESH_TOKEN|CLIENT_SECRET|SECRET_KEY|PASSWORD))["']?\s*[:=]\s*["']([^\s"']{16,})["']"""
        ),
    ),
    (
        "credential_assignment",
        re.compile(
            r"(?m)^(?:export\s+)?[A-Z0-9_]{0,64}(?:API_KEY|ACCESS_TOKEN|REFRESH_TOKEN|CLIENT_SECRET|SECRET_KEY|PASSWORD)\s*=\s*([^\s'\"#]{16,})\s*$"
        ),
    ),
)
PLACEHOLDER = re.compile(
    r"(?i)(?:example|placeholder|your[-_]|replace[-_]|insert[-_]|test[-_]|dummy|fake|changeme|<|\$\{)"
)


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    category: str
    scope: str
    line: int | None = None


def path_category(path: str) -> str | None:
    """Classify publication-sensitive paths, including archive prefixes."""
    normalized = path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    parts = tuple(part.lower() for part in pure.parts)
    if pure.is_absolute() or ".." in parts or re.match(r"^[A-Za-z]:", normalized):
        return "unsafe_path"
    if not parts:
        return None
    name = parts[-1]
    if any(part in PRIVATE_DIRS for part in parts[:-1]):
        return "private_or_generated_directory"
    if "papers" in parts[:-1] and name != ".gitkeep":
        return "local_paper_library"
    if parts[-2:] == ("config", "papers_keywords.json") or ".local." in name:
        return "local_configuration"
    if name != ".env.example" and (
        name == ".env" or name.startswith(".env.") or name.endswith(".env")
    ):
        return "environment_configuration"
    if name in AUTH_NAMES or re.fullmatch(
        r"(?:credentials[-_].*|client_secret.*|service[-_]account.*)\.json", name
    ):
        return "authentication_file"
    if name in RUNTIME_NAMES:
        return "private_or_runtime_file"
    if PurePosixPath(name).suffix in PRIVATE_SUFFIXES:
        return "private_or_runtime_file"
    if name.endswith((".db-journal", ".db-wal", ".db-shm", ".pyc", ".pyo")):
        return "private_or_runtime_file"
    if name.endswith(".pdf"):
        return "third_party_or_local_pdf"
    return None


def scan_bytes(path: str, data: bytes, scope: str) -> list[Finding]:
    """Inspect UTF-8 text while keeping all matched content out of reports."""
    category = path_category(path)
    if category:
        return [Finding(path, category, scope)]
    if len(data) > MAX_FILE_BYTES:
        return [Finding(path, "file_exceeds_scan_limit", scope)]
    if b"\0" in data:
        return []
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return []
    found = set()
    for category, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(content):
            value = match.group(1) if match.lastindex else match.group()
            if category == "credential_assignment" and PLACEHOLDER.search(value):
                continue
            found.add(Finding(path, category, scope, content.count("\n", 0, match.start()) + 1))
    return sorted(found)


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    if result.returncode:
        # Git stderr may contain remote URLs or credential helpers. Never echo it.
        raise RuntimeError("Git inspection failed; run this command inside a Git repository.")
    return result.stdout


def candidate_files(root: Path) -> list[str]:
    """Return tracked plus non-ignored untracked relative paths that still exist.

    This intentionally retains tracked ignored files so publication checks can
    reject them. Deleted worktree files are omitted; symlinks remain inspectable.
    """
    paths = set(
        _git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0")
    )
    return [
        path
        for raw in sorted(paths - {b""})
        if (path := raw.decode("utf-8", errors="surrogateescape"))
        and ((root / path).exists() or (root / path).is_symlink())
    ]


def _scan_files(root: Path, paths: Iterable[str], scope: str) -> list[Finding]:
    findings = []
    for path in paths:
        file = root / path
        if file.is_symlink():
            findings.append(Finding(path, "symlink_not_publishable", scope))
        elif file.is_file():
            category = path_category(path)
            if category:
                findings.append(Finding(path, category, scope))
            elif file.stat().st_size > MAX_FILE_BYTES:
                findings.append(Finding(path, "file_exceeds_scan_limit", scope))
            else:
                findings.extend(scan_bytes(path, file.read_bytes(), scope))
    return findings


def scan_worktree(root: Path) -> list[Finding]:
    return _scan_files(root, candidate_files(root), "worktree")


def scan_directory(root: Path) -> list[Finding]:
    """Inspect every file in a prepared export directory, without Git ignores."""
    return _scan_files(
        root,
        sorted(
            str(path.relative_to(root))
            for path in root.rglob("*")
            if not path.is_dir() or path.is_symlink()
        ),
        "directory",
    )


def scan_index(root: Path) -> list[Finding]:
    findings = []
    for entry in _git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        metadata, path_bytes = entry.split(b"\t", 1)
        mode, oid, stage = metadata.decode().split()
        path = path_bytes.decode("utf-8", errors="surrogateescape")
        if stage != "0":
            findings.append(Finding(path, "unmerged_index", "index"))
        elif mode != "100644" and mode != "100755":
            findings.append(Finding(path, "non_regular_index_entry", "index"))
        else:
            findings.extend(_scan_blob(root, oid, path, "index"))
    return findings


def _scan_blob(root: Path, oid: str, path: str, scope: str) -> list[Finding]:
    category = path_category(path)
    if category:
        return [Finding(path, category, scope)]
    size = int(_git(root, "cat-file", "-s", oid))
    if size > MAX_FILE_BYTES:
        return [Finding(path, "file_exceeds_scan_limit", scope)]
    return scan_bytes(path, _git(root, "cat-file", "blob", oid), scope)


def scan_history(root: Path) -> list[Finding]:
    """Scan every reachable blob/path pair, including deleted and renamed files."""
    findings = set()
    seen = set()
    commits = _git(root, "rev-list", "--all").decode().splitlines()
    for commit in commits:
        for item in _git(root, "ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
            if not item:
                continue
            metadata, raw_path = item.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            path = raw_path.decode("utf-8", errors="surrogateescape")
            identity = (oid, path)
            if identity in seen:
                continue
            seen.add(identity)
            scope = "history:" + commit[:12]
            if kind != "blob" or mode not in {"100644", "100755"}:
                findings.add(Finding(path, "non_regular_history_entry", scope))
            else:
                findings.update(_scan_blob(root, oid, path, scope))
    return sorted(findings)


def scan_archive(archive: Path) -> list[Finding]:
    """Read archive members in place; never extract paths or follow links."""
    findings = []
    scope = "archive:" + archive.name
    count = total = 0

    def inspect(path: str, size: int, regular: bool, reader):
        nonlocal count, total
        count += 1
        total += size
        if count > MAX_ARCHIVE_MEMBERS or total > MAX_ARCHIVE_BYTES:
            raise ValueError("Archive exceeds inspection limits.")
        category = path_category(path)
        if category:
            findings.append(Finding(path, category, scope))
        elif not regular:
            findings.append(Finding(path, "non_regular_archive_entry", scope))
        elif size > MAX_FILE_BYTES:
            findings.append(Finding(path, "file_exceeds_scan_limit", scope))
        else:
            with reader() as stream:
                findings.extend(scan_bytes(path, stream.read(MAX_FILE_BYTES + 1), scope))

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as package:
            for item in package.infolist():
                if item.is_dir():
                    continue
                mode = item.external_attr >> 16
                inspect(
                    item.filename,
                    item.file_size,
                    mode & 0o170000 in {0, 0o100000},
                    lambda item=item: package.open(item),
                )
    else:
        with tarfile.open(archive) as package:
            for item in package:
                if item.isdir():
                    continue
                inspect(
                    item.name,
                    item.size,
                    item.isfile(),
                    lambda item=item: package.extractfile(item),
                )
    return findings


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Git working directory")
    parser.add_argument("--json", action="store_true", help="Redacted machine-readable report")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--staged", action="store_true", help="Check all index content, not disk")
    group.add_argument("--history", action="store_true", help="Audit all reachable Git commits")
    group.add_argument(
        "--archive", type=Path, nargs="+", help="Check ZIP, tar, wheel or sdist files"
    )
    args = parser.parse_args(argv)
    try:
        if args.archive:
            findings = [finding for path in args.archive for finding in scan_archive(path)]
        elif args.history:
            findings = scan_history(args.root)
        elif args.staged:
            findings = scan_index(args.root)
        else:
            findings = scan_worktree(args.root)
    except (RuntimeError, OSError, ValueError, tarfile.TarError, zipfile.BadZipFile):
        print(
            "Release inspection failed; no files were changed. Check the input and limits.",
            file=sys.stderr,
        )
        return 2
    findings = sorted(set(findings))
    if args.json:
        print(json.dumps({"ok": not findings, "findings": [asdict(f) for f in findings]}, indent=2))
    elif findings:
        for finding in findings:
            location = f":{finding.line}" if finding.line is not None else ""
            print(f"BLOCK {finding.scope} {finding.path}{location} [{finding.category}]")
        print(f"Release check blocked: {len(findings)} findings. Matched values are never printed.")
    else:
        print("Release check passed for the selected scope.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())

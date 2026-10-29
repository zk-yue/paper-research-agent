import io
import json
import subprocess
import tarfile
import zipfile

import pytest

from scripts import check_release


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True).stdout


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "init", "-q")
    return tmp_path


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "project/.codex/auth.json",
        "project/task_data/task/reviews/result.md",
        "project/papers/download.pdf",
        "config/papers_keywords.json",
        "credentials.json",
        ".git-credentials",
        "storage_state.json",
        "nested/client_secret_local.json",
        "backend/data/prompt_logs/log.json",
        "project/.git/config",
        "../escape.py",
        "C:/private/config.json",
    ],
)
def test_private_paths_blocked_in_source_and_prefixed_archives(path):
    assert check_release.path_category(path)


@pytest.mark.parametrize(
    "path", [".env.example", "config/papers_keywords.example.json", "papers/.gitkeep", "README.md"]
)
def test_public_templates_are_allowed(path):
    assert check_release.path_category(path) is None


def test_gitignore_does_not_hide_previously_tracked_private_data(repository):
    (repository / "task_data").mkdir()
    (repository / "task_data/run.json").write_text("{}")
    git(repository, "add", "task_data/run.json")
    (repository / ".gitignore").write_text("task_data/\n.env\n")
    (repository / ".env").write_text("private configuration")
    candidates = check_release.candidate_files(repository)
    assert "task_data/run.json" in candidates
    assert ".env" not in candidates
    assert any(f.path == "task_data/run.json" for f in check_release.scan_worktree(repository))


def test_staged_scan_uses_index_not_cleaned_worktree(repository):
    secret = "sk-" + "A" * 40
    path = repository / "settings.py"
    path.write_text(f'KEY = "{secret}"\n')
    git(repository, "add", "settings.py")
    path.write_text('KEY = "safe-example"\n')
    assert not check_release.scan_worktree(repository)
    assert any(f.category == "openai_key" for f in check_release.scan_index(repository))


def test_history_finds_deleted_sensitive_file(repository):
    (repository / "auth.json").write_text("{}")
    git(repository, "add", "auth.json")
    git(
        repository,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    git(repository, "rm", "auth.json")
    git(
        repository,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "remove fixture",
    )
    assert not check_release.scan_worktree(repository)
    assert any(f.path == "auth.json" for f in check_release.scan_history(repository))


def test_new_repository_has_no_history_or_staged_files(repository):
    assert check_release.scan_history(repository) == []
    assert check_release.scan_index(repository) == []


def test_redacted_report_does_not_print_secret(repository, capsys):
    secret = "ghp_" + "Q" * 40
    (repository / "config.py").write_text(f'VALUE = "{secret}"\n')
    assert check_release.main(["--root", str(repository), "--json"]) == 1
    output = capsys.readouterr().out
    assert secret not in output
    report = json.loads(output)
    assert report["findings"] == [
        {"path": "config.py", "category": "github_token", "scope": "worktree", "line": 1}
    ]


def test_template_placeholder_does_not_hide_actual_token():
    data = b"OPENAI_API_KEY=your_api_key_here\n"
    assert not check_release.scan_bytes(".env.example", data, "test")
    secret = "sk-" + "p" * 40
    findings = check_release.scan_bytes(".env.example", data + secret.encode(), "test")
    assert any(f.category == "openai_key" for f in findings)


def test_export_directory_scan_ignores_gitignore_rules(tmp_path):
    (tmp_path / ".gitignore").write_text(".env\n")
    (tmp_path / ".env").write_text("private")
    assert any(f.path == ".env" for f in check_release.scan_directory(tmp_path))


def test_source_symlink_rejected_without_reading_external_target(repository, tmp_path):
    external = tmp_path.parent / "external-private.txt"
    external.write_text("outside repository")
    (repository / "code.py").symlink_to(external)
    findings = check_release.scan_worktree(repository)
    assert [(f.path, f.category) for f in findings] == [("code.py", "symlink_not_publishable")]


def test_zip_and_wheel_inspected_without_extraction(tmp_path):
    archive = tmp_path / "distribution.whl"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("package/app.py", "print('hello')")
        package.writestr("package/task_data/private.json", "{}")
        package.writestr("../escape.txt", "unsafe")
    findings = check_release.scan_archive(archive)
    assert {f.category for f in findings} == {"private_or_generated_directory", "unsafe_path"}
    assert not (tmp_path / "package").exists()
    assert not (tmp_path.parent / "escape.txt").exists()


def test_archive_symlink_rejected_without_following_target(tmp_path):
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as package:
        regular = tarfile.TarInfo("project/main.py")
        content = b"print('hello')"
        regular.size = len(content)
        package.addfile(regular, io.BytesIO(content))
        link = tarfile.TarInfo("project/linked.py")
        link.type = tarfile.SYMTYPE
        link.linkname = "/outside/private"
        package.addfile(link)
    findings = check_release.scan_archive(archive)
    assert [(f.path, f.category) for f in findings] == [
        ("project/linked.py", "non_regular_archive_entry")
    ]


def test_oversized_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(check_release, "MAX_FILE_BYTES", 10)
    archive = tmp_path / "large.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("data.txt", "x" * 11)
    assert check_release.scan_archive(archive)[0].category == "file_exceeds_scan_limit"


def test_archive_total_limit_reports_failure_without_content(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(check_release, "MAX_ARCHIVE_BYTES", 10)
    archive = tmp_path / "large.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("data.txt", "x" * 11)
    assert check_release.main(["--archive", str(archive)]) == 2
    captured = capsys.readouterr()
    assert "inspection failed" in captured.err
    assert not captured.out

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from backend import preset_store, prompt_log, template_store
from backend.task_paths import ensure_task_dir, resolve_metadata_file
from history import save_history
from storage import InvalidIdentifier, atomic_write_json, json_path


@pytest.mark.parametrize("identifier", ["../secret", "/tmp/secret", "..", "a/b", "a\\b", "", "a.b"])
def test_unsafe_ids_are_rejected(tmp_path, identifier):
    with pytest.raises(InvalidIdentifier):
        json_path(tmp_path, identifier)
    with pytest.raises(InvalidIdentifier):
        ensure_task_dir(identifier)


def test_symlink_cannot_escape_storage(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    (root / "secret.json").symlink_to(tmp_path / "outside.json")
    with pytest.raises(InvalidIdentifier):
        json_path(root, "secret")


def test_failed_replace_keeps_original_json_and_cleans_temporary(tmp_path):
    path = tmp_path / "entry.json"
    atomic_write_json(path, {"message": "中文"})
    with patch("storage.os.replace", side_effect=OSError("disk failure")):
        with pytest.raises(OSError):
            atomic_write_json(path, {"message": "replacement"})
    assert json.loads(path.read_text()) == {"message": "中文"}
    assert list(tmp_path.iterdir()) == [path]


def test_unserializable_data_does_not_damage_previous_file(tmp_path):
    path = tmp_path / "entry.json"
    atomic_write_json(path, {"ok": True})
    with pytest.raises(TypeError):
        atomic_write_json(path, {"invalid": object()})
    assert json.loads(path.read_text()) == {"ok": True}


def test_legacy_task_metadata_migrates_without_losing_files():
    from backend import task_paths

    task_paths.TASKS_DIR.mkdir()
    legacy = task_paths.TASKS_DIR / "old123.json"
    atomic_write_json(legacy, {"id": "old123", "keywords": ["触觉"]})
    old = task_paths.TASKS_DIR / "old123"
    (old / "papers").mkdir(parents=True)
    (old / "papers" / "paper.pdf").write_bytes(b"%PDF-test")
    new = ensure_task_dir("old123", ["触觉"])
    assert (new / "papers" / "paper.pdf").read_bytes() == b"%PDF-test"
    assert resolve_metadata_file("old123") == new / "task.json"
    assert not legacy.exists()


def test_preset_and_template_crud_and_corrupt_file_recovery():
    preset = preset_store.create_preset(" label ", "prompt")
    template = template_store.create_template(" Journal ", [{"title": "Paper", "content": "Text"}])
    (preset_store.PRESETS_DIR / "broken.json").write_text("{")
    (template_store.TEMPLATES_DIR / "broken.json").write_text("{")
    assert len(preset_store.list_presets()) == 1
    assert len(template_store.list_templates()) == 1
    assert preset_store.update_preset(preset["id"], content="updated")["content"] == "updated"
    assert template_store.get_template(template["id"])["journal_name"] == "Journal"
    assert preset_store.delete_preset(preset["id"])
    assert template_store.delete_template(template["id"])


def test_prompt_log_parallel_calls_use_entry_id():
    first = prompt_log.record_prompt("task123", "same-agent", "", "first")
    second = prompt_log.record_prompt("task123", "same-agent", "", "second")
    prompt_log.record_prompt(
        "task123", "same-agent", "", "", "result1", status="success", entry_id=first["id"]
    )
    prompt_log.record_prompt(
        "task123", "same-agent", "", "", "result2", status="success", entry_id=second["id"]
    )
    entries = prompt_log.get_log("task123")
    assert [(e["user_prompt"], e["response_preview"]) for e in entries] == [
        ("first", "result1"),
        ("second", "result2"),
    ]
    entries[0]["status"] = "changed externally"
    assert prompt_log.get_log("task123")[0]["status"] == "success"


def test_concurrent_log_and_history_writes_keep_all_records():
    def write(index):
        prompt_log.record_prompt("task123", f"agent{index}", "", str(index))
        return save_history(["keyword"], 2025, "review", "papers", "log")

    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = list(executor.map(write, range(20)))
    assert len(set(ids)) == 20
    assert len(prompt_log.get_log("task123")) == 20
    prompt_log._store.clear()
    assert len(prompt_log.get_log("task123")) == 20

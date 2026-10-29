import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.task_manager import task_manager
from settings import ServerSettings


@pytest.fixture
def client(monkeypatch):
    from backend.routes import tasks

    async def fake_execution(task_id):
        task_manager.update_task(task_id, status="completed")

    monkeypatch.setattr(tasks, "execute_task", fake_execution)
    with TestClient(create_app()) as client:
        yield client


def test_health_and_task_lifecycle(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    response = client.post("/api/tasks", json={"keywords": [" tactile ", "tactile", ""]})
    assert response.status_code == 200
    task_id = response.json()["task_id"]
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["keywords"] == ["tactile"]
    assert client.get("/api/tasks").json()[0]["id"] == task_id
    assert client.delete(f"/api/tasks/{task_id}").json()["deleted"]
    assert client.get(f"/api/tasks/{task_id}").status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"keywords": []},
        {"keywords": ["  "]},
        {"search_mode": "deep", "topic": "  "},
        {"keywords": ["test"], "deep_rounds": 0},
        {"keywords": ["test"], "deep_rounds": 6},
        {"keywords": ["test"], "year_from": -1},
    ],
)
def test_invalid_task_input_is_rejected_before_background_work(client, payload):
    assert client.post("/api/tasks", json=payload).status_code == 422
    assert not task_manager.tasks


def test_deep_task_accepts_topic_and_uses_it_as_initial_keyword(client):
    response = client.post("/api/tasks", json={"search_mode": "deep", "topic": "  robot sensing  "})
    assert response.status_code == 200
    task = client.get("/api/tasks/" + response.json()["task_id"]).json()
    assert task["topic"] == "robot sensing"
    assert task["keywords"] == ["robot sensing"]


def test_templates_and_presets_still_use_existing_routes(client):
    template = client.post(
        "/api/review-templates", json={"journal_name": "Test", "papers": []}
    ).json()
    assert client.get("/api/review-templates/" + template["id"]).status_code == 200
    assert (
        client.put(
            "/api/review-templates/" + template["id"], json={"journal_name": "Updated"}
        ).json()["journal_name"]
        == "Updated"
    )
    assert client.delete("/api/review-templates/" + template["id"]).status_code == 200
    preset = client.post(
        "/api/instruction-presets", json={"label": "Test", "content": "Prompt"}
    ).json()
    assert (
        client.put("/api/instruction-presets/" + preset["id"], json={"content": "Updated"}).json()[
            "content"
        ]
        == "Updated"
    )
    assert client.delete("/api/instruction-presets/" + preset["id"]).status_code == 200
    assert client.get("/api/review-templates/bad%5Cpath").status_code == 400


def test_websocket_missing_task_does_not_leak_subscription(client):
    with client.websocket_connect("/ws/missing") as websocket:
        assert websocket.receive_json() == {"type": "not_found"}
    assert not task_manager.ws_subscribers


def test_websocket_disconnect_cleans_up(client):
    task = task_manager.create_task(keywords=["test"])
    with client.websocket_connect("/ws/" + task.id) as websocket:
        assert websocket.receive_json()["type"] == "init"
        assert task_manager.ws_subscribers[task.id]
    # Application shutdown also waits for request tasks to finish.
    # TestClient's websocket context waits for the server coroutine's cleanup.
    assert not task_manager.ws_subscribers


def test_cors_does_not_allow_arbitrary_origin(client):
    response = client.options(
        "/api/tasks",
        headers={"Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in response.headers
    response = client.options(
        "/api/tasks",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_spa_fallback_does_not_mask_unknown_api_routes(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>test</html>")
    (dist / "favicon.svg").write_text("<svg/>")
    with TestClient(create_app(ServerSettings(frontend_dist=dist))) as client:
        assert client.get("/tasks/abc").status_code == 200
        assert client.get("/favicon.svg").text == "<svg/>"
        response = client.get("/api/does-not-exist")
        assert response.status_code == 404
        assert response.json()["detail"] == "Not found"


def test_importing_server_does_not_create_data(tmp_path):
    data = tmp_path / "not-created"
    env = {**os.environ, "PAPER_RESEARCH_DATA_DIR": str(data)}
    result = subprocess.run(
        [sys.executable, "-c", "import backend.server"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert not data.exists()


def test_task_summary_excludes_large_results(client):
    from backend.schemas import PaperInfo

    task = task_manager.create_task(keywords=["summary"])
    task_manager.update_task(
        task.id,
        papers=[PaperInfo(title="Paper", summary_cn="long abstract")],
        results={"custom_reviews": [{"content": "long review"}], "report": "large report"},
    )
    summary = client.get("/api/tasks?summary=true").json()[0]
    assert summary["paper_count"] == 1
    assert summary["review_count"] == 1
    assert "papers" not in summary and "results" not in summary
    assert client.get("/api/tasks").json()[0]["papers"][0]["title"] == "Paper"


def test_stop_preserves_task_results_and_is_idempotent(client):
    from backend.schemas import PaperInfo

    task = task_manager.create_task(keywords=["stop"])
    task_manager.update_task(
        task.id, papers=[PaperInfo(title="Saved paper")], results={"note": "keep"}
    )
    assert client.post(f"/api/tasks/{task.id}/cancel").json()["status"] == "cancelled"
    task_after = client.get(f"/api/tasks/{task.id}").json()
    assert task_after["papers"][0]["title"] == "Saved paper"
    assert task_after["results"]["note"] == "keep"
    assert client.post(f"/api/tasks/{task.id}/cancel").status_code == 200
    assert client.post("/api/tasks/missing/cancel").status_code == 404


def test_retry_preserves_previous_run_and_rejects_active_run(client):
    task = task_manager.create_task(keywords=["retry"], year_from=2020, quick_mode=True)
    assert client.post(f"/api/tasks/{task.id}/retry").status_code == 409
    task_manager.update_task(task.id, status="failed", results={"note": "previous results"})
    response = client.post(f"/api/tasks/{task.id}/retry")
    assert response.status_code == 200
    new_id = response.json()["task_id"]
    assert new_id != task.id
    assert client.get(f"/api/tasks/{task.id}").json()["results"]["note"] == "previous results"
    new_task = client.get(f"/api/tasks/{new_id}").json()
    assert new_task["keywords"] == ["retry"]
    assert new_task["year_from"] == 2020 and new_task["quick_mode"]


def test_runtime_reports_provider_without_credentials(client, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "codex")
    monkeypatch.setenv("CODEX_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-returned")
    assert client.get("/api/runtime").json() == {
        "provider": "codex",
        "model": "test-model",
        "incremental_output": False,
    }

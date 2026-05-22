import asyncio
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.schemas import TaskEvent


def test_upload_endpoint_returns_file_metadata(tmp_path, monkeypatch) -> None:
    from backend.app import main
    from backend.app.memory.store import MemoryPaths, MemoryStore

    monkeypatch.setattr(main, "memory", MemoryStore(MemoryPaths(root=tmp_path)))
    with TestClient(app) as client:
        response = client.post(
            "/api/uploads",
            data={"user_id": "user-a", "session_id": "session-a"},
            files={"files": ("sample.txt", b"hello", "text/plain")},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["events_url"].startswith("/api/uploads/")
    assert payload["files"][0]["filename"] == "sample.txt"
    assert payload["files"][0]["content_type"] == "text/plain"
    assert payload["files"][0]["size"] == 5


def test_upload_endpoint_runs_intake_for_proteomics_matrix(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from backend.app import main
    from backend.app.memory.store import MemoryPaths, MemoryStore

    monkeypatch.setattr(main, "memory", MemoryStore(MemoryPaths(root=tmp_path)))
    matrix = (
        b"Protein.Names,Genes,First.Protein.Description,WT1,WT2,MT1,MT2\n"
        b"P1,G1,protein one,10,11,20,21\n"
        b"P2,G2,protein two,9,10,18,19\n"
        b"P3,G3,protein three,7,8,17,18\n"
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/uploads",
            data={"user_id": "user-a", "session_id": "session-a"},
            files={"files": ("proteomics.csv", matrix, "text/csv")},
        )

        assert response.status_code == 200
        payload = response.json()
        events = client.get(payload["events_url"]).text.splitlines()
    result_line = next(line for index, line in enumerate(events) if events[index - 1] == "event: result")
    intake = json.loads(result_line.removeprefix("data: "))["data"]["files"][0]["intake"]
    assert intake["status"] == "ready"
    assert intake["data_family"] == "proteomics"
    assert intake["sample_groups"] == {"WT": ["WT1", "WT2"], "MT": ["MT1", "MT2"]}
    assert intake["attempts"][0]["status"] == "completed"
    assert Path(intake["standard_files"]["matrix"]).is_file()


def test_chat_endpoint_delegates_to_task_manager(monkeypatch) -> None:
    from backend.app import main

    calls = {}

    class FakeTasks:
        def create_task(self, message, user_id, session_id, history, attachments, detached_files, web_search=False):
            calls["message"] = message
            calls["user_id"] = user_id
            calls["session_id"] = session_id
            calls["history"] = history
            calls["attachments"] = attachments
            calls["detached_files"] = detached_files
            calls["web_search"] = web_search
            return "task-1"

    monkeypatch.setattr(main, "tasks", FakeTasks())
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={
            "message": "hello",
            "user_id": "user-a",
            "session_id": "session-a",
            "history": [{"role": "user", "content": "previous"}],
            "attachments": [
                {
                    "file_id": "file-a",
                    "filename": "a.txt",
                    "content_type": "text/plain",
                    "size": 3,
                    "path": "memory/path/a.txt",
                }
            ],
            "detached_files": [{"file_id": "file-b", "filename": "removed.csv"}],
            "web_search": True,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"task_id": "task-1", "events_url": "/api/tasks/task-1/events"}
    assert calls["message"] == "hello"
    assert calls["user_id"] == "user-a"
    assert calls["session_id"] == "session-a"
    assert calls["history"][0].content == "previous"
    assert calls["attachments"][0].filename == "a.txt"
    assert calls["detached_files"][0].filename == "removed.csv"
    assert calls["web_search"] is True


def test_cancel_endpoint_delegates_to_task_manager(monkeypatch) -> None:
    from backend.app import main

    calls = {}

    class FakeTasks:
        def get(self, task_id):
            calls["get"] = task_id
            return SimpleNamespace(done=False)

        def cancel(self, task_id):
            calls["cancel"] = task_id
            return True

    monkeypatch.setattr(main, "tasks", FakeTasks())
    client = TestClient(app)

    response = client.post("/api/tasks/task-stop/cancel")

    assert response.status_code == 200
    assert response.json() == {"task_id": "task-stop", "cancelled": True}
    assert calls == {"get": "task-stop", "cancel": "task-stop"}


def test_task_events_replay_only_events_after_last_event_id(monkeypatch) -> None:
    from backend.app import main

    state = SimpleNamespace(
        events=[
            TaskEvent(type="progress", step=1, status="开始"),
            TaskEvent(type="answer_delta", step=7, status="输出中", data={"delta": "answer"}),
            TaskEvent(type="result", step=7, status="完成", data={"answer": "answer"}),
        ],
        done=True,
        condition=asyncio.Condition(),
    )

    class FakeTasks:
        def get(self, task_id):
            return state if task_id == "task-replay" else None

    monkeypatch.setattr(main, "tasks", FakeTasks())
    client = TestClient(app)

    response = client.get("/api/tasks/task-replay/events", headers={"Last-Event-ID": "1"})

    assert response.status_code == 200
    assert "event: progress" not in response.text
    assert "event: answer_delta" in response.text
    assert "event: result" in response.text
    assert "event: end" in response.text

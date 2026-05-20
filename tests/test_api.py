from fastapi.testclient import TestClient

from backend.app.main import app


def test_upload_endpoint_returns_file_metadata(tmp_path, monkeypatch) -> None:
    from backend.app import main
    from backend.app.memory.store import MemoryPaths, MemoryStore

    monkeypatch.setattr(main, "memory", MemoryStore(MemoryPaths(root=tmp_path)))
    client = TestClient(app)

    response = client.post(
        "/api/uploads",
        data={"user_id": "user-a", "session_id": "session-a"},
        files={"files": ("sample.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["files"][0]["filename"] == "sample.txt"
    assert payload["files"][0]["content_type"] == "text/plain"
    assert payload["files"][0]["size"] == 5


def test_chat_endpoint_delegates_to_task_manager(monkeypatch) -> None:
    from backend.app import main

    calls = {}

    class FakeTasks:
        def create_task(self, message, user_id, session_id, history, attachments):
            calls["message"] = message
            calls["user_id"] = user_id
            calls["session_id"] = session_id
            calls["history"] = history
            calls["attachments"] = attachments
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
        },
    )

    assert response.status_code == 200
    assert response.json() == {"task_id": "task-1", "events_url": "/api/tasks/task-1/events"}
    assert calls["message"] == "hello"
    assert calls["user_id"] == "user-a"
    assert calls["session_id"] == "session-a"
    assert calls["history"][0].content == "previous"
    assert calls["attachments"][0].filename == "a.txt"

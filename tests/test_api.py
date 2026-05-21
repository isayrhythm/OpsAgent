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


def test_upload_endpoint_runs_intake_for_proteomics_matrix(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from backend.app import main
    from backend.app.memory.store import MemoryPaths, MemoryStore

    monkeypatch.setattr(main, "memory", MemoryStore(MemoryPaths(root=tmp_path)))
    client = TestClient(app)
    matrix = (
        b"Protein.Names,Genes,First.Protein.Description,WT1,WT2,MT1,MT2\n"
        b"P1,G1,protein one,10,11,20,21\n"
        b"P2,G2,protein two,9,10,18,19\n"
        b"P3,G3,protein three,7,8,17,18\n"
    )

    response = client.post(
        "/api/uploads",
        data={"user_id": "user-a", "session_id": "session-a"},
        files={"files": ("proteomics.csv", matrix, "text/csv")},
    )

    assert response.status_code == 200
    intake = response.json()["files"][0]["intake"]
    assert intake["status"] == "ready"
    assert intake["data_family"] == "proteomics"
    assert intake["sample_groups"] == {"WT": ["WT1", "WT2"], "MT": ["MT1", "MT2"]}
    assert intake["attempts"][0]["status"] == "completed"
    assert Path(intake["standard_files"]["matrix"]).is_file()


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

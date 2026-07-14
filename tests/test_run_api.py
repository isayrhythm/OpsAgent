import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.schemas import TaskEvent


class FakeRunState:
    def __init__(self) -> None:
        self.id = "run-a"
        self.done = True
        self.events = [
            TaskEvent(type="progress", step=1, status="Running"),
            TaskEvent(type="result", step=100, status="Completed", data={"answer": "done"}),
        ]
        self.condition = asyncio.Condition()

    def summary(self, *, include_result=False):
        value = {
            "run_id": self.id,
            "status": "completed",
            "events_url": f"/api/runs/{self.id}/events",
        }
        if include_result:
            value["result"] = {"answer": "done"}
        return value


def test_background_run_api_lists_reads_and_cancels(monkeypatch) -> None:
    from backend.app import main

    state = FakeRunState()
    calls = {}

    class FakeRuns:
        def list_for_session(self, user_id, session_id):
            calls["list"] = (user_id, session_id)
            return [state]

        def get(self, run_id):
            calls["get"] = run_id
            return state if run_id == state.id else None

        def cancel(self, run_id):
            calls["cancel"] = run_id
            return True

    monkeypatch.setattr(main, "runs", FakeRuns())
    client = TestClient(app)

    listed = client.get("/api/runs", params={"user_id": "user-a", "session_id": "session-a"})
    fetched = client.get("/api/runs/run-a")
    cancelled = client.post("/api/runs/run-a/cancel")

    assert listed.json()[0]["run_id"] == "run-a"
    assert fetched.json()["result"]["answer"] == "done"
    assert cancelled.json() == {"run_id": "run-a", "cancelled": True}
    assert calls["list"] == ("user-a", "session-a")
    assert calls["cancel"] == "run-a"


def test_background_run_events_replay(monkeypatch) -> None:
    from backend.app import main

    state = FakeRunState()
    monkeypatch.setattr(main, "runs", SimpleNamespace(get=lambda run_id: state if run_id == state.id else None))
    client = TestClient(app)

    response = client.get("/api/runs/run-a/events", headers={"Last-Event-ID": "1"})

    assert response.status_code == 200
    assert "event: progress" not in response.text
    assert "event: result" in response.text
    assert "event: end" in response.text

import asyncio

from backend.app.memory.store import MemoryPaths, MemoryStore
from backend.app.runs.manager import RunManager


def test_run_manager_tracks_progress_and_appends_completion(tmp_path) -> None:
    async def run() -> None:
        memory = MemoryStore(MemoryPaths(root=tmp_path))
        manager = RunManager(memory=memory, retention_seconds=-1)
        started = asyncio.Event()
        finish = asyncio.Event()

        async def worker(emit):
            await emit("progress", 25, "Checking outputs", {"stage": "quality_control"})
            started.set()
            await finish.wait()
            return {"run_status": "completed", "answer": "analysis ready", "artifacts": {"report": "x"}}

        state = manager.create_run(
            user_id="user-a",
            session_id="session-a",
            run_type="omics_analysis",
            agent="transcriptomics",
            title="Transcriptomics",
            worker=worker,
        )
        await started.wait()

        prompt = manager.prompt_context("user-a", "session-a")
        assert prompt[0]["run_id"] == state.id
        assert prompt[0]["status"] == "running"
        assert prompt[0]["status_text"] == "Checking outputs"

        finish.set()
        await state.runner

        assert state.status == "completed"
        assert state.result["answer"] == "analysis ready"
        assert state.events[-1].type == "result"
        history = memory.load_history("user-a", "session-a")
        assert history[-1].content == "analysis ready"

    asyncio.run(run())


def test_run_manager_cancels_running_worker() -> None:
    async def run() -> None:
        manager = RunManager(retention_seconds=-1)
        started = asyncio.Event()

        async def worker(_emit):
            started.set()
            await asyncio.Event().wait()
            return {}

        state = manager.create_run(
            user_id="user-a",
            session_id="session-a",
            run_type="omics_analysis",
            agent="proteomics",
            title="Proteomics",
            worker=worker,
        )
        await started.wait()
        assert manager.cancel(state.id) is True
        await state.runner

        assert state.status == "cancelled"
        assert state.events[-1].type == "cancelled"

    asyncio.run(run())

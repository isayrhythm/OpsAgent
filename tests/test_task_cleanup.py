import asyncio

from backend.app.agents.task_manager import TaskManager, TaskState
from backend.app.services.upload_intake_manager import UploadIntakeManager


def test_task_manager_discards_completed_tasks_after_retention() -> None:
    async def run() -> None:
        manager = TaskManager(retention_seconds=0)
        state = TaskState(
            id="task-a",
            message="hello",
            user_id="user-a",
            session_id="session-a",
            history=[],
            attachments=[],
            detached_files=[],
            web_search=False,
            web_search_mode="auto",
            web_search_providers=[],
        )
        manager._tasks[state.id] = state

        await manager._mark_done(state)
        await asyncio.sleep(0.01)

        assert manager.get(state.id) is None

    asyncio.run(run())


def test_upload_intake_manager_discards_completed_tasks_after_retention() -> None:
    async def run() -> None:
        manager = UploadIntakeManager(retention_seconds=0)

        task_id = manager.create_task([], lambda _summary: None)
        await asyncio.sleep(0.01)

        assert manager.get(task_id) is None

    asyncio.run(run())

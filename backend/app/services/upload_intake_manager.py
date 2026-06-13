from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from backend.app.config import UPLOAD_INTAKE_RETENTION_SECONDS
from backend.app.schemas import TaskEvent, UploadedFileSummary
from backend.app.tools.file_context import inspect_uploaded_file


UploadMetadataWriter = Callable[[UploadedFileSummary], None]


@dataclass
class UploadIntakeState:
    id: str
    uploads: list[UploadedFileSummary]
    write_metadata: UploadMetadataWriter
    queue: asyncio.Queue[TaskEvent] = field(default_factory=asyncio.Queue)
    done: bool = False


class UploadIntakeManager:
    def __init__(self, retention_seconds: float = UPLOAD_INTAKE_RETENTION_SECONDS) -> None:
        self._tasks: dict[str, UploadIntakeState] = {}
        self._retention_seconds = retention_seconds

    def create_task(
        self,
        uploads: list[UploadedFileSummary],
        write_metadata: UploadMetadataWriter,
    ) -> str:
        task_id = uuid.uuid4().hex
        state = UploadIntakeState(id=task_id, uploads=uploads, write_metadata=write_metadata)
        self._tasks[task_id] = state
        asyncio.create_task(self._run(state))
        return task_id

    def get(self, task_id: str) -> UploadIntakeState | None:
        return self._tasks.get(task_id)

    def discard(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)

    async def _emit(self, state: UploadIntakeState, event_type: str, step: int, status: str, data: Any = None) -> None:
        await state.queue.put(TaskEvent(type=event_type, step=step, status=status, data=data))

    async def _run(self, state: UploadIntakeState) -> None:
        ready: list[UploadedFileSummary] = []
        try:
            await self._emit(state, "progress", 1, f"Reading File Context for {len(state.uploads)} upload(s)")
            for index, item in enumerate(state.uploads, start=1):
                await self._emit(state, "progress", 2, f"Inspecting File: {item.filename} ({index}/{len(state.uploads)})")
                intake = await asyncio.to_thread(inspect_uploaded_file, item)
                ready_item = item.model_copy(update={"intake": intake})
                await asyncio.to_thread(state.write_metadata, ready_item)
                ready.append(ready_item)
                await self._emit(
                    state,
                    "progress",
                    3,
                    f"File Context {intake.get('status', 'completed')}: {item.filename}",
                    {"file_id": item.file_id, "intake_status": intake.get("status", "unknown")},
                )
            await self._emit(
                state,
                "result",
                4,
                "File Context Ready",
                {"files": [item.model_dump(mode="json") for item in ready]},
            )
        except Exception as exc:
            await self._emit(state, "error", 999, f"File Context failed: {exc}")
        finally:
            state.done = True
            asyncio.create_task(self._discard_after_retention(state.id))

    async def _discard_after_retention(self, task_id: str) -> None:
        if self._retention_seconds > 0:
            await asyncio.sleep(self._retention_seconds)
        state = self._tasks.get(task_id)
        if state is not None and state.done:
            self.discard(task_id)

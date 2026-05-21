from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from backend.app.memory.store import MemoryStore
from backend.app.schemas import ChatHistoryMessage, UploadedFileSummary
from backend.app.schemas import TaskEvent
from backend.app.services.agent_graph import build_agent_graph
from backend.app.services.deepseek_client import DeepSeekClient


@dataclass
class TaskState:
    id: str
    message: str
    user_id: str
    session_id: str | None
    history: list[ChatHistoryMessage]
    attachments: list[UploadedFileSummary]
    queue: asyncio.Queue[TaskEvent] = field(default_factory=asyncio.Queue)
    done: bool = False


class TaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskState] = {}
        self._memory = MemoryStore()

    def create_task(
        self,
        message: str,
        user_id: str,
        session_id: str | None,
        history: list[ChatHistoryMessage],
        attachments: list[UploadedFileSummary],
    ) -> str:
        task_id = uuid.uuid4().hex
        state = TaskState(
            id=task_id,
            message=message,
            user_id=user_id,
            session_id=session_id,
            history=history,
            attachments=attachments,
        )
        self._tasks[task_id] = state
        asyncio.create_task(self._run(state))
        return task_id

    def get(self, task_id: str) -> TaskState | None:
        return self._tasks.get(task_id)

    async def _emit(self, state: TaskState, event_type: str, step: int, status: str, data: Any = None) -> None:
        await state.queue.put(TaskEvent(type=event_type, step=step, status=status, data=data))

    async def _run(self, state: TaskState) -> None:
        llm = DeepSeekClient()
        try:
            if state.session_id:
                self._memory.ensure_user_dirs(state.user_id)
                if not state.history:
                    state.history = self._memory.load_history(state.user_id, state.session_id)
                if not state.attachments:
                    state.attachments = self._memory.load_uploads(state.user_id, state.session_id)
            graph = build_agent_graph(llm, lambda event_type, step, status, data=None: self._emit(
                state,
                event_type,
                step,
                status,
                data,
            ))
            result = await graph.ainvoke(
                {"message": state.message, "history": state.history, "attachments": state.attachments}
            )
            answer = result.get("answer") or ""
            if state.session_id and answer:
                self._memory.append_exchange(
                    state.user_id,
                    state.session_id,
                    state.message,
                    answer,
                    result.get("attachments") or state.attachments,
                )
            await self._emit(
                state,
                "result",
                7,
                "完成",
                {
                    "skill": result.get("skill_name"),
                    "skills": result.get("skill_names") or ([result.get("skill_name")] if result.get("skill_name") else []),
                    "skill_output": result.get("skill_output"),
                    "skill_outputs": result.get("skill_outputs"),
                    "answer": answer,
                    "mode": "skill" if result.get("skill_name") else "chat",
                },
            )
        except Exception as exc:
            await self._emit(state, "error", 999, f"任务失败: {exc}")
        finally:
            state.done = True

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from backend.app.schemas import ChatHistoryMessage
from backend.app.schemas import TaskEvent
from backend.app.services.agent_graph import build_agent_graph
from backend.app.services.deepseek_client import DeepSeekClient


@dataclass
class TaskState:
    id: str
    message: str
    session_id: str | None
    history: list[ChatHistoryMessage]
    queue: asyncio.Queue[TaskEvent] = field(default_factory=asyncio.Queue)
    done: bool = False


class TaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskState] = {}

    def create_task(
        self,
        message: str,
        session_id: str | None,
        history: list[ChatHistoryMessage],
    ) -> str:
        task_id = uuid.uuid4().hex
        state = TaskState(id=task_id, message=message, session_id=session_id, history=history)
        self._tasks[task_id] = state
        asyncio.create_task(self._run(state))
        return task_id

    def get(self, task_id: str) -> TaskState | None:
        return self._tasks.get(task_id)

    async def _emit(self, state: TaskState, event_type: str, step: int, status: str, data: Any = None) -> None:
        await state.queue.put(TaskEvent(type=event_type, step=step, status=status, data=data))

    async def _stream_answer(self, state: TaskState, answer: str) -> None:
        chunk = ""
        for char in answer:
            chunk += char
            if len(chunk) >= 4 or char in "\n。！？.!?":
                await self._emit(state, "answer_delta", 7, "输出中", {"delta": chunk})
                chunk = ""
                await asyncio.sleep(0.018)
        if chunk:
            await self._emit(state, "answer_delta", 7, "输出中", {"delta": chunk})

    async def _run(self, state: TaskState) -> None:
        llm = DeepSeekClient()
        try:
            graph = build_agent_graph(llm, lambda event_type, step, status, data=None: self._emit(
                state,
                event_type,
                step,
                status,
                data,
            ))
            result = await graph.ainvoke({"message": state.message, "history": state.history})
            answer = result.get("answer") or ""
            await self._stream_answer(state, answer)
            await self._emit(
                state,
                "result",
                7,
                "完成",
                {
                    "skill": result.get("skill_name"),
                    "skill_output": result.get("skill_output"),
                    "answer": answer,
                    "mode": "skill" if result.get("skill_name") else "chat",
                },
            )
        except Exception as exc:
            await self._emit(state, "error", 999, f"任务失败: {exc}")
        finally:
            state.done = True

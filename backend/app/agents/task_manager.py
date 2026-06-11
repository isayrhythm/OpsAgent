from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from backend.app.memory.store import MemoryStore
from backend.app.schemas import ChatHistoryMessage, DetachedFileSummary, UploadedFileSummary
from backend.app.schemas import TaskEvent
from backend.app.agents.agent_graph import build_agent_graph
from backend.app.services.deepseek_client import DeepSeekClient


@dataclass
class TaskState:
    id: str
    message: str
    user_id: str
    session_id: str | None
    history: list[ChatHistoryMessage]
    attachments: list[UploadedFileSummary]
    detached_files: list[DetachedFileSummary]
    web_search: bool
    web_search_mode: str
    web_search_providers: list[str]
    events: list[TaskEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    runner: asyncio.Task[None] | None = None
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
        detached_files: list[DetachedFileSummary],
        web_search: bool = False,
        web_search_mode: str = "auto",
        web_search_providers: list[str] | None = None,
    ) -> str:
        task_id = uuid.uuid4().hex
        state = TaskState(
            id=task_id,
            message=message,
            user_id=user_id,
            session_id=session_id,
            history=history,
            attachments=attachments,
            detached_files=detached_files,
            web_search=web_search,
            web_search_mode=web_search_mode,
            web_search_providers=web_search_providers or [],
        )
        self._tasks[task_id] = state
        state.runner = asyncio.create_task(self._run(state))
        return task_id

    def get(self, task_id: str) -> TaskState | None:
        return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        state = self.get(task_id)
        if state is None or state.done or state.runner is None or state.runner.done():
            return False
        state.runner.cancel()
        return True

    async def _emit(self, state: TaskState, event_type: str, step: int, status: str, data: Any = None) -> None:
        async with state.condition:
            state.events.append(TaskEvent(type=event_type, step=step, status=status, data=data))
            state.condition.notify_all()

    async def _mark_done(self, state: TaskState) -> None:
        async with state.condition:
            state.done = True
            state.condition.notify_all()

    async def _run(self, state: TaskState) -> None:
        llm = DeepSeekClient()
        try:
            if state.session_id:
                self._memory.ensure_user_dirs(state.user_id)
                if not state.history:
                    state.history = self._memory.load_history(state.user_id, state.session_id)
            graph = build_agent_graph(llm, lambda event_type, step, status, data=None: self._emit(
                state,
                event_type,
                step,
                status,
                data,
            ))
            result = await graph.ainvoke(
                {
                    "message": state.message,
                    "history": state.history,
                    "attachments": state.attachments,
                    "detached_files": state.detached_files,
                    "search": {
                        "force": state.web_search,
                        "mode": state.web_search_mode,
                        "providers": state.web_search_providers,
                    },
                }
            )
            answer = result.get("answer") or ""
            search_result = result.get("search") if isinstance(result.get("search"), dict) else {}
            search_result = {key: value for key, value in search_result.items() if key != "task"}
            research_result = result.get("research") if isinstance(result.get("research"), dict) else {}
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
                    "web_sources": research_result.get("sources") or search_result.get("sources") or result.get("web_sources") or [],
                    "mode": "deep_research"
                    if research_result
                    else ("web_search" if search_result.get("enabled") else ("skill" if result.get("skill_name") else "chat")),
                    "web_search_mode": search_result.get("mode") or state.web_search_mode,
                    "web_search_providers": search_result.get("providers") or state.web_search_providers,
                    "web_search_plan": search_result.get("plan"),
                    "search": search_result,
                    "research": research_result,
                    "usage": llm.usage_snapshot(),
                },
            )
        except asyncio.CancelledError:
            await self._emit(state, "cancelled", 999, "任务已停止")
        except Exception as exc:
            await self._emit(state, "error", 999, f"任务失败: {exc}")
        finally:
            await self._mark_done(state)

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.app.config import BACKGROUND_RUN_RETENTION_SECONDS
from backend.app.memory.store import MemoryStore
from backend.app.schemas import TaskEvent


RunEmit = Callable[[str, int, str, Any | None], Awaitable[None]]
RunWorker = Callable[[RunEmit], Awaitable[dict[str, Any]]]
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BackgroundRunState:
    id: str
    user_id: str
    session_id: str | None
    run_type: str
    agent: str
    title: str
    status: str = "queued"
    status_text: str = "Queued"
    progress_step: int = 0
    progress_data: Any | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    events: list[TaskEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    runner: asyncio.Task[None] | None = None
    result: dict[str, Any] | None = None

    @property
    def done(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def summary(self, *, include_result: bool = False) -> dict[str, Any]:
        value: dict[str, Any] = {
            "run_id": self.id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "run_type": self.run_type,
            "agent": self.agent,
            "title": self.title,
            "status": self.status,
            "status_text": self.status_text,
            "progress_step": self.progress_step,
            "progress_data": self.progress_data,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "events_url": f"/api/runs/{self.id}/events",
        }
        if include_result:
            value["result"] = self.result
        return value

    def prompt_summary(self) -> dict[str, Any]:
        value = self.summary()
        result = self.result if isinstance(self.result, dict) else {}
        answer = str(result.get("answer") or "")
        if answer:
            value["result_summary"] = answer[:1200]
        value["result_available"] = bool(result)
        return value


class RunManager:
    def __init__(
        self,
        *,
        memory: MemoryStore | None = None,
        retention_seconds: float = BACKGROUND_RUN_RETENTION_SECONDS,
    ) -> None:
        self._runs: dict[str, BackgroundRunState] = {}
        self._memory = memory
        self._retention_seconds = retention_seconds

    def create_run(
        self,
        *,
        user_id: str,
        session_id: str | None,
        run_type: str,
        agent: str,
        title: str,
        worker: RunWorker,
    ) -> BackgroundRunState:
        run_id = uuid.uuid4().hex
        state = BackgroundRunState(
            id=run_id,
            user_id=user_id,
            session_id=session_id,
            run_type=run_type,
            agent=agent,
            title=title,
        )
        state.events.append(
            TaskEvent(
                type="progress",
                step=0,
                status="Queued",
                data={"agent": agent, "agent_state": "queued", "run": state.summary()},
            )
        )
        self._runs[run_id] = state
        state.runner = asyncio.create_task(self._run(state, worker))
        return state

    def get(self, run_id: str) -> BackgroundRunState | None:
        return self._runs.get(run_id)

    def list_for_session(
        self,
        user_id: str,
        session_id: str | None,
        *,
        include_completed: bool = True,
    ) -> list[BackgroundRunState]:
        runs = [
            state
            for state in self._runs.values()
            if state.user_id == user_id and state.session_id == session_id
        ]
        if not include_completed:
            runs = [state for state in runs if not state.done]
        return sorted(runs, key=lambda state: state.created_at, reverse=True)

    def prompt_context(self, user_id: str, session_id: str | None, limit: int = 6) -> list[dict[str, Any]]:
        runs = self.list_for_session(user_id, session_id)
        active = sorted((state for state in runs if not state.done), key=lambda state: state.created_at, reverse=True)
        completed = sorted((state for state in runs if state.done), key=lambda state: state.created_at, reverse=True)
        return [state.prompt_summary() for state in (active + completed)[:limit]]

    def cancel(self, run_id: str) -> bool:
        state = self.get(run_id)
        if state is None or state.done or state.runner is None or state.runner.done():
            return False
        state.runner.cancel()
        return True

    def discard(self, run_id: str) -> None:
        self._runs.pop(run_id, None)

    async def _emit(
        self,
        state: BackgroundRunState,
        event_type: str,
        step: int,
        status: str,
        data: Any = None,
    ) -> None:
        async with state.condition:
            state.status_text = status
            state.progress_step = step
            if event_type == "progress":
                state.progress_data = data
            state.updated_at = _now()
            event_data = data
            if isinstance(data, dict):
                event_data = {**data, "run": state.summary()}
            state.events.append(TaskEvent(type=event_type, step=step, status=status, data=event_data))
            state.condition.notify_all()

    async def _run(self, state: BackgroundRunState, worker: RunWorker) -> None:
        try:
            state.status = "running"
            await self._emit(
                state,
                "progress",
                1,
                "Running",
                {"agent": state.agent, "agent_state": "running"},
            )
            payload = await worker(
                lambda event_type, step, status, data=None: self._emit(state, event_type, step, status, data)
            )
            state.result = payload
            requested_status = str(payload.get("run_status") or "completed")
            state.status = requested_status if requested_status in {"completed", "failed"} else "completed"
            final_status = "Completed" if state.status == "completed" else "Failed"
            await self._emit(
                state,
                "result",
                100,
                final_status,
                payload,
            )
            self._append_completion_to_memory(state)
        except asyncio.CancelledError:
            state.status = "cancelled"
            await self._emit(
                state,
                "cancelled",
                999,
                "Cancelled",
                {},
            )
        except Exception as exc:
            state.status = "failed"
            state.result = {"error": str(exc), "answer": f"后台任务失败：{exc}"}
            await self._emit(
                state,
                "error",
                999,
                f"Failed: {exc}",
                {"error": str(exc), "answer": state.result["answer"]},
            )
            self._append_completion_to_memory(state)
        finally:
            state.updated_at = _now()
            if self._retention_seconds >= 0:
                asyncio.create_task(self._discard_after_retention(state.id))

    def _append_completion_to_memory(self, state: BackgroundRunState) -> None:
        if self._memory is None or not state.session_id or not isinstance(state.result, dict):
            return
        answer = str(state.result.get("answer") or "").strip()
        if answer:
            self._memory.append_assistant_message(
                state.user_id,
                state.session_id,
                answer,
                run_id=state.id,
            )

    async def _discard_after_retention(self, run_id: str) -> None:
        if self._retention_seconds > 0:
            await asyncio.sleep(self._retention_seconds)
        state = self._runs.get(run_id)
        if state is not None and state.done:
            self.discard(run_id)

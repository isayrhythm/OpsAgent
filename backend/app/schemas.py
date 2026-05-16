from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant", "agent"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    user_id: str = Field(default="default", min_length=1)
    session_id: str | None = None
    history: list[ChatHistoryMessage] = Field(default_factory=list)


class ChatResponse(BaseModel):
    task_id: str
    events_url: str


class SkillSummary(BaseModel):
    name: str
    description: str
    version: str
    trigger: str
    execution_mode: str
    data_paths: list[str]
    path: str


class TaskEvent(BaseModel):
    type: Literal["progress", "thinking_delta", "answer_delta", "result", "error"]
    step: int
    status: str
    data: Any | None = None

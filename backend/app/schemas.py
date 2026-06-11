from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant", "agent"]
    content: str = Field(min_length=1)


class UploadedFileSummary(BaseModel):
    file_id: str
    filename: str
    content_type: str | None = None
    size: int
    path: str | None = None
    intake: dict[str, Any] | None = None


class DetachedFileSummary(BaseModel):
    file_id: str
    filename: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    user_id: str = Field(default="default", min_length=1)
    session_id: str | None = None
    history: list[ChatHistoryMessage] = Field(default_factory=list)
    attachments: list[UploadedFileSummary] = Field(default_factory=list)
    detached_files: list[DetachedFileSummary] = Field(default_factory=list)
    web_search: bool = False
    web_search_mode: Literal["off", "auto", "force"] = "auto"
    web_search_providers: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    task_id: str
    events_url: str


class UploadResponse(BaseModel):
    task_id: str
    events_url: str
    files: list[UploadedFileSummary]


class SkillSummary(BaseModel):
    name: str
    description: str
    version: str
    trigger: str
    execution_mode: str
    data_paths: list[str]
    path: str


class TaskEvent(BaseModel):
    type: Literal["progress", "thinking_delta", "answer_delta", "ui_delta", "source_delta", "result", "cancelled", "error"]
    step: int
    status: str
    data: Any | None = None

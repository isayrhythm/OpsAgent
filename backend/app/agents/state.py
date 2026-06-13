from __future__ import annotations

from typing import Any, Awaitable, Callable, TypedDict

from backend.app.schemas import ChatHistoryMessage, DetachedFileSummary, UploadedFileSummary
from backend.app.services.skill_loader import SkillSpec


Emit = Callable[[str, int, str, Any | None], Awaitable[None]]


class AgentState(TypedDict, total=False):
    message: str
    history: list[ChatHistoryMessage]
    attachments: list[UploadedFileSummary]
    detached_files: list[DetachedFileSummary]
    skills: list[SkillSpec]
    skill_name: str | None
    skill_names: list[str]
    route_reason: str
    data_profiles: list[dict[str, Any]]
    skill_output: dict[str, Any]
    skill_outputs: list[dict[str, Any]]
    command_outputs: list[dict[str, Any]]
    route_mode: str
    research: dict[str, Any]
    search: dict[str, Any]
    answer: str

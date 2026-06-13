from __future__ import annotations

from typing import Any

from backend.app.agents.state import AgentState
from backend.app.llm.deepseek import DeepSeekClient


def make_route_node(llm: DeepSeekClient, deps: Any):
    async def route_node(state: AgentState) -> AgentState:
        if deps.should_route_deep_research(state["message"]):
            return {
                "skill_name": None,
                "skill_names": [],
                "route_mode": "deep_research",
                "route_reason": "deep research intent",
            }
        selection = await deps.route_registered_skills(
            message=state["message"],
            skills=state.get("skills", []),
            llm=llm,
            history=state.get("history", []),
            data_profiles=state.get("data_profiles", []),
            detached_files=state.get("detached_files", []),
        )
        selected_skills = selection.get("skills") or []
        if any(getattr(skill, "name", "") == deps.COMMAND_TOOL_NAME for skill in selected_skills):
            return {**selection, "route_mode": "command"}
        if not selection.get("skills"):
            return {**selection, "route_mode": "chat"}
        return {**selection, "route_mode": "skill"}

    return route_node

from __future__ import annotations

import asyncio
from typing import Any

from backend.app.agents.state import AgentState, Emit
from backend.app.llm.deepseek import DeepSeekClient


def normalize_search_state(state: AgentState, deps: Any) -> dict[str, Any]:
    search = state.get("search") if isinstance(state.get("search"), dict) else {}
    mode = deps.normalize_web_search_mode(
        search.get("mode") or state.get("web_search_mode"),
        legacy_web_search=bool(search.get("force") or state.get("web_search")),
    )
    providers = search.get("providers") or state.get("web_search_providers") or []
    return {"mode": mode, "providers": providers}


def make_intake_uploads_node(llm: DeepSeekClient, emit: Emit, deps: Any):
    async def intake_uploads_node(state: AgentState) -> AgentState:
        updates: AgentState = {}
        search_state = normalize_search_state(state, deps)
        search_mode = search_state["mode"]
        search_plan = await deps.plan_web_search(
            state["message"],
            history=state.get("history", []),
            mode=search_mode,
            providers=search_state.get("providers", []),
            llm=llm,
        )
        search_state = {**search_state, "enabled": search_plan.need_search, "plan": search_plan.to_dict()}
        if search_plan.need_search:
            queries = [query.query for query in search_plan.queries]
            query_label = "; ".join(queries[:2])
            await emit(
                "progress",
                1,
                f"Running Web Search: {query_label}" if query_label else "Running Web Search",
                {"agent": "Web Search", "agent_state": "running", "queries": queries},
            )
            search_state["task"] = asyncio.create_task(
                deps.search_web_queries(
                    [query.to_dict() for query in search_plan.queries],
                    history=state.get("history", []),
                    providers=search_state.get("providers", []),
                )
            )
        updates["search"] = search_state

        attachments = state.get("attachments", [])
        if not attachments:
            return {**updates, "attachments": [], "data_profiles": []}
        await emit(
            "progress",
            1,
            "Reading File Context",
            {"agent": "File Inspector", "agent_state": "running", "files": [item.filename for item in attachments]},
        )
        ready_attachments = await asyncio.to_thread(deps.ensure_attachment_intakes, attachments)
        data_profiles = deps.profile_uploaded_files(ready_attachments)
        await emit("progress", 2, "File Context Ready", {"agent": "File Inspector", "agent_state": "done"})
        return {**updates, "attachments": ready_attachments, "data_profiles": data_profiles}

    return intake_uploads_node


def make_load_skill_node(emit: Emit, deps: Any):
    async def load_skill_node(_state: AgentState) -> AgentState:
        await emit("progress", 2, "Thinking", None)
        skills = [*deps.load_skill_registry(), deps.command_tool_spec()]
        return {"skills": skills}

    return load_skill_node

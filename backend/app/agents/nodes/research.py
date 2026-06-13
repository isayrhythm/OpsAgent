from __future__ import annotations

from typing import Any

from backend.app.agents.nodes.intake import normalize_search_state
from backend.app.agents.state import AgentState, Emit
from backend.app.llm.deepseek import DeepSeekClient


def make_research_node(llm: DeepSeekClient, emit: Emit, deps: Any):
    async def research_node(state: AgentState) -> AgentState:
        research_graph = deps.build_research_graph(llm, emit)
        result = await research_graph.ainvoke(
            {
                "message": state["message"],
                "history": state.get("history", []),
                "providers": normalize_search_state(state, deps).get("providers", []),
                "skills": state.get("skills", []),
            }
        )
        answer = result.get("answer") or ""
        return {
            "answer": answer,
            "research": {
                "intent": result.get("intent"),
                "plan": result.get("plan"),
                "tasks": result.get("completed_tasks"),
                "evaluations": result.get("evaluations"),
                "sources": result.get("sources") or [],
            },
            "web_sources": result.get("sources") or [],
        }

    return research_node

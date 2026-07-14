from __future__ import annotations

from types import SimpleNamespace

from langgraph.graph import END, StateGraph

from backend.app.agents.deep_research import build_research_graph, should_route_deep_research
from backend.app.agents.nodes.answer import make_answer_node
from backend.app.agents.nodes.command import make_command_node
from backend.app.agents.nodes.intake import make_intake_uploads_node, make_load_skill_node
from backend.app.agents.nodes.research import make_research_node
from backend.app.agents.nodes.route import make_route_node
from backend.app.agents.nodes.skill import make_execute_node
from backend.app.agents.skill_registry import load_skill_registry, route_registered_skills
from backend.app.agents.skill_result_adapter import answer_ready_output, ui_block_events
from backend.app.agents.state import AgentState, Emit
from backend.app.llm.calls import complete_text
from backend.app.llm.deepseek import DeepSeekClient
from backend.app.services.code_executor import execute_skill, retry_skill
from backend.app.services.id_mapping import enrich_skill_output_with_id_mapping
from backend.app.services.result_evaluator import compact_value, evaluate_skill_result
from backend.app.tools.command_tool import COMMAND_TOOL_NAME, command_tool_spec, execute_shell_command, plan_shell_command
from backend.app.tools.file_context import ensure_attachment_intakes, profile_uploaded_files, uploaded_files_prompt
from backend.app.tools.tool_runner import ToolRetryPolicy, run_tool
from backend.app.tools.web_search import (
    WEB_SEARCH_ANSWER_REQUIREMENTS,
    format_web_search_context,
    search_web_queries,
    web_search_sources,
)
from backend.app.tools.web_search_planner import normalize_web_search_mode, plan_web_search


def build_agent_graph(llm: DeepSeekClient, emit: Emit, run_manager: Any | None = None):
    graph = StateGraph(AgentState)
    deps = _deps(run_manager)

    graph.add_node("intake_uploads", make_intake_uploads_node(llm, emit, deps))
    graph.add_node("load_skills", make_load_skill_node(emit, deps))
    graph.add_node("route", make_route_node(llm, deps))
    graph.add_node("execute_skill", make_execute_node(llm, emit, deps))
    graph.add_node("execute_command", make_command_node(llm, emit, deps))
    graph.add_node("research_graph", make_research_node(llm, emit, deps))
    graph.add_node("final_answer", make_answer_node(llm, emit, deps))

    graph.set_entry_point("intake_uploads")
    graph.add_edge("intake_uploads", "load_skills")
    graph.add_edge("load_skills", "route")
    graph.add_conditional_edges(
        "route",
        _next_node_after_route,
        {
            "research_graph": "research_graph",
            "execute_command": "execute_command",
            "execute_skill": "execute_skill",
            "final_answer": "final_answer",
        },
    )
    graph.add_edge("execute_skill", "final_answer")
    graph.add_edge("execute_command", "final_answer")
    graph.add_edge("research_graph", END)
    graph.add_edge("final_answer", END)
    return graph.compile()


def _next_node_after_route(state: AgentState) -> str:
    if state.get("route_mode") == "deep_research":
        return "research_graph"
    if state.get("route_mode") == "command":
        return "execute_command"
    if state.get("skill_name"):
        return "execute_skill"
    return "final_answer"


def _deps(run_manager: Any | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        COMMAND_TOOL_NAME=COMMAND_TOOL_NAME,
        ToolRetryPolicy=ToolRetryPolicy,
        answer_ready_output=answer_ready_output,
        build_research_graph=build_research_graph,
        command_tool_spec=command_tool_spec,
        compact_value=compact_value,
        complete_text=complete_text,
        enrich_skill_output_with_id_mapping=enrich_skill_output_with_id_mapping,
        ensure_attachment_intakes=ensure_attachment_intakes,
        evaluate_skill_result=evaluate_skill_result,
        execute_shell_command=execute_shell_command,
        execute_skill=execute_skill,
        format_web_search_context=format_web_search_context,
        load_skill_registry=load_skill_registry,
        llm_factory=DeepSeekClient,
        normalize_web_search_mode=normalize_web_search_mode,
        plan_shell_command=plan_shell_command,
        plan_web_search=plan_web_search,
        profile_uploaded_files=profile_uploaded_files,
        retry_skill=retry_skill,
        route_registered_skills=route_registered_skills,
        run_manager=run_manager,
        run_tool=run_tool,
        search_web_queries=search_web_queries,
        should_route_deep_research=should_route_deep_research,
        ui_block_events=ui_block_events,
        uploaded_files_prompt=uploaded_files_prompt,
        web_search_answer_requirements=WEB_SEARCH_ANSWER_REQUIREMENTS,
        web_search_sources=web_search_sources,
    )

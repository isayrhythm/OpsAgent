from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, TypedDict

from langgraph.graph import END, StateGraph

from backend.app.llm.prompts import FINAL_ANSWER_SYSTEM_PROMPT, GENERAL_CHAT_SYSTEM_PROMPT
from backend.app.schemas import ChatHistoryMessage, DetachedFileSummary, UploadedFileSummary
from backend.app.services.code_executor import SkillCodeExecutionError, execute_skill, retry_skill
from backend.app.services.data_intake import (
    ensure_attachment_intakes,
    profile_uploaded_files,
    uploaded_files_prompt,
)
from backend.app.services.deepseek_client import DeepSeekClient
from backend.app.services.result_evaluator import compact_value, evaluate_skill_result
from backend.app.services.router import route_skill
from backend.app.services.skill_loader import SkillSpec, load_skill, load_skill_catalog
from backend.app.services.web_search import format_web_search_context, search_web, web_search_sources


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
    web_search: bool
    web_search_task: Any
    web_sources: list[dict[str, Any]]
    answer: str


def build_agent_graph(llm: DeepSeekClient, emit: Emit):
    graph = StateGraph(AgentState)

    def llm_history_messages(state: AgentState) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for item in state.get("history", [])[-20:]:
            role = "assistant" if item.role in {"assistant", "agent"} else "user"
            messages.append({"role": role, "content": item.content})
        messages.append({"role": "user", "content": state["message"]})
        return messages

    def attachment_context(state: AgentState) -> list[dict[str, Any]]:
        return [
            {
                "file_id": item.file_id,
                "filename": item.filename,
                "content_type": item.content_type,
                "size": item.size,
                "path": item.path,
            }
            for item in state.get("attachments", [])
        ]

    def detached_files_prompt(state: AgentState) -> str:
        detached = state.get("detached_files", [])
        if not detached:
            return ""
        filenames = "、".join(item.filename for item in detached[-8:])
        return f"当前对话已卸载文件：{filenames}。这些文件当前不可用；若历史消息说它们还在，以当前附件状态为准。"

    async def intake_uploads_node(state: AgentState) -> AgentState:
        updates: AgentState = {}
        if state.get("web_search"):
            await emit("progress", 1, "正在搜索网页", None)
            updates["web_search_task"] = asyncio.create_task(search_web(state["message"]))

        attachments = state.get("attachments", [])
        if not attachments:
            return {**updates, "attachments": [], "data_profiles": []}
        await emit("progress", 1, "正在整理上传文件", None)
        ready_attachments = await asyncio.to_thread(ensure_attachment_intakes, attachments)
        data_profiles = profile_uploaded_files(ready_attachments)
        await emit("progress", 2, "上传文件已完成 intake", None)
        return {**updates, "attachments": ready_attachments, "data_profiles": data_profiles}

    async def load_skill_node(state: AgentState) -> AgentState:
        await emit("progress", 2, "正在理解请求", None)
        skills = load_skill_catalog()
        await emit(
            "progress",
            2,
            "正在判断是否需要专门能力",
            None,
        )
        return {"skills": skills}

    async def route_node(state: AgentState) -> AgentState:
        await emit("progress", 3, "正在路由请求", None)
        skills = state.get("skills", [])
        decision = await route_skill(
            state["message"],
            skills,
            llm,
            state.get("history", []),
            state.get("data_profiles", []),
            state.get("detached_files", []),
        )
        selected_skills = decision.skills
        if not selected_skills:
            await emit("progress", 4, "使用普通对话模式", None)
            return {"skill_name": None, "skill_names": [], "skills": [], "route_reason": decision.reason}
        await emit(
            "progress",
            4,
            "正在调用专门能力",
            None,
        )
        loaded_skills = [load_skill(skill.path) for skill in selected_skills]
        return {
            "skill_name": loaded_skills[0].name,
            "skill_names": [skill.name for skill in loaded_skills],
            "skills": loaded_skills,
            "route_reason": decision.reason,
        }

    async def run_one_skill(skill: SkillSpec, state: AgentState) -> dict[str, Any]:
        await emit("progress", 5, f"正在调用 {skill.name} 智能体", {"agent": skill.name, "agent_state": "running"})
        try:
            skill_output = await execute_skill(
                state["message"],
                skill,
                llm,
                emit,
                history=state.get("history", []),
                attachments=state.get("attachments", []),
                data_profiles=state.get("data_profiles", []),
            )
            evaluation = await evaluate_skill_result(
                user_message=state["message"],
                resolved_message=state["message"],
                skill=skill,
                result=skill_output.get("result"),
                llm=llm,
            )
        except Exception as exc:
            first_error = str(exc)
            first_code = exc.code if isinstance(exc, SkillCodeExecutionError) else None
            evaluation = await evaluate_skill_result(
                user_message=state["message"],
                resolved_message=state["message"],
                skill=skill,
                result=None,
                llm=llm,
                error=first_error,
            )
            if evaluation.get("category") != "retry_code" or skill.execution_mode.startswith("deterministic"):
                await emit("progress", 5, f"{skill.name} 智能体已完成", {"agent": skill.name, "agent_state": "done"})
                return {
                    "skill_name": skill.name,
                    "output": {
                        "mode": "execution_failed",
                        "result": None,
                        "error": first_error,
                        "code": first_code,
                        "evaluation": evaluation,
                    },
                }
            await emit(
                "progress",
                5,
                f"正在重新调用 {skill.name} 智能体",
                {"agent": skill.name, "agent_state": "running", "retry": True, "reason": evaluation.get("reason")},
            )
            try:
                skill_output = await retry_skill(
                    state["message"],
                    skill,
                    llm,
                    previous_code=first_code,
                    previous_error=first_error,
                    evaluation=evaluation,
                    emit=emit,
                )
            except Exception as retry_exc:
                retry_code = retry_exc.code if isinstance(retry_exc, SkillCodeExecutionError) else None
                await emit("progress", 5, f"{skill.name} 智能体已完成", {"agent": skill.name, "agent_state": "done"})
                return {
                    "skill_name": skill.name,
                    "output": {
                        "mode": "retry_failed",
                        "result": None,
                        "error": str(retry_exc),
                        "code": retry_code,
                        "evaluation": {
                            "category": "not_found",
                            "answered": False,
                            "reason": "重试后仍未能得到可用结果",
                            "missing": ["valid_skill_result"],
                        },
                    }
                }
            evaluation = await evaluate_skill_result(
                user_message=state["message"],
                resolved_message=state["message"],
                skill=skill,
                result=skill_output.get("result"),
                llm=llm,
            )
            skill_output["evaluation"] = evaluation
            await emit("progress", 5, f"{skill.name} 智能体已完成", {"agent": skill.name, "agent_state": "done"})
            return {"skill_name": skill.name, "output": skill_output}

        if evaluation.get("category") == "retry_code" and skill.execution_mode.startswith("deterministic"):
            skill_output["evaluation"] = {
                **evaluation,
                "category": "partial",
                "retry_instruction": "",
            }
            await emit("progress", 5, f"{skill.name} 智能体已完成", {"agent": skill.name, "agent_state": "done"})
            return {"skill_name": skill.name, "output": skill_output}

        if evaluation.get("category") == "retry_code":
            await emit(
                "progress",
                5,
                f"正在重新调用 {skill.name} 智能体",
                {"agent": skill.name, "agent_state": "running", "retry": True, "reason": evaluation.get("reason")},
            )
            try:
                skill_output = await retry_skill(
                    state["message"],
                    skill,
                    llm,
                    previous_code=skill_output.get("code"),
                    previous_result=skill_output.get("result"),
                    evaluation=evaluation,
                    emit=emit,
                )
            except Exception as retry_exc:
                retry_code = retry_exc.code if isinstance(retry_exc, SkillCodeExecutionError) else None
                await emit("progress", 5, f"{skill.name} 智能体已完成", {"agent": skill.name, "agent_state": "done"})
                return {
                    "skill_name": skill.name,
                    "output": {
                        "mode": "retry_failed",
                        "result": skill_output.get("result"),
                        "error": str(retry_exc),
                        "code": retry_code,
                        "evaluation": {
                            "category": "partial",
                            "answered": False,
                            "reason": "第一次结果不足，重试后仍未能得到更好的可用结果",
                            "missing": ["valid_retry_result"],
                        },
                    }
                }
            evaluation = await evaluate_skill_result(
                user_message=state["message"],
                resolved_message=state["message"],
                skill=skill,
                result=skill_output.get("result"),
                llm=llm,
            )
        skill_output["evaluation"] = evaluation
        await emit("progress", 5, f"{skill.name} 智能体已完成", {"agent": skill.name, "agent_state": "done"})
        return {"skill_name": skill.name, "output": skill_output}

    async def execute_node(state: AgentState) -> AgentState:
        skills = state.get("skills", [])
        if not skills:
            return {"skill_outputs": []}
        if len(skills) == 1:
            item = await run_one_skill(skills[0], state)
            return {"skill_output": item["output"], "skill_outputs": [item]}
        outputs = await asyncio.gather(*(run_one_skill(skill, state) for skill in skills))
        return {"skill_output": outputs[0]["output"], "skill_outputs": list(outputs)}

    async def emit_ui_blocks(state: AgentState) -> None:
        for item in state.get("skill_outputs", []):
            result = item.get("output", {}).get("result")
            if not isinstance(result, dict):
                continue
            for block in result.get("ui_blocks", []):
                if not isinstance(block, dict) or block.get("type") != "gene_function_research_path":
                    continue
                block_header = {key: value for key, value in block.items() if key != "steps"}
                await emit("ui_delta", 6, "正在绘制研究路径", {"action": "start", "block": block_header})
                for step in block.get("steps", []):
                    if isinstance(step, dict):
                        await emit(
                            "ui_delta",
                            6,
                            "正在绘制研究路径",
                            {"action": "step", "block_id": block.get("id"), "step": step},
                        )

    def answer_ready_output(value: Any) -> Any:
        if not isinstance(value, dict):
            return compact_value(value)
        result = value.get("result")
        if isinstance(result, dict) and result.get("analysis") == "gene_phenotype_prediction":
            answer_result = {
                key: item
                for key, item in result.items()
                if key in {"status", "analysis", "query", "top_k", "species_searched", "genes", "not_found"}
            }
            answer_result["gene_mappings"] = result.get("gene_mappings", [])
            answer_result["matches"] = [
                {
                    "input": match.get("input"),
                    "species": match.get("species"),
                    "species_label": match.get("species_label"),
                    "canonical_id": match.get("canonical_id"),
                    "matched_by": match.get("matched_by"),
                    "top_k": match.get("top_k"),
                    "predictions": [
                        {
                            "rank": item.get("rank"),
                            "phenotype": item.get("phenotype"),
                            "pred_score": item.get("pred_score"),
                        }
                        for item in match.get("predictions", [])
                        if isinstance(item, dict)
                    ],
                }
                for match in result.get("matches", [])
                if isinstance(match, dict)
            ]
            return {**value, "result": answer_result}
        if not isinstance(result, dict) or not result.get("ui_blocks"):
            return compact_value(value)
        answer_result = {key: item for key, item in result.items() if key not in {"matches", "ui_blocks"}}
        answer_result["matches"] = [
            {
                "paper_id": match.get("paper_id"),
                "title": match.get("title"),
                "gene_id": match.get("gene_id"),
                "step_count": len(match.get("steps", [])),
            }
            for match in result.get("matches", [])
            if isinstance(match, dict)
        ]
        answer_result["visualized_ui_blocks"] = [
            {
                "type": block.get("type"),
                "gene_id": block.get("gene_id"),
                "title": block.get("title"),
                "step_count": len(block.get("steps", [])),
            }
            for block in result.get("ui_blocks", [])
            if isinstance(block, dict)
        ]
        return compact_value({**value, "result": answer_result})

    async def build_web_context(state: AgentState) -> dict[str, Any]:
        if not state.get("web_search"):
            return {"context": "", "sources": []}
        await emit("progress", 6, "正在整合搜索结果", None)
        search_task = state.get("web_search_task")
        search_result = await search_task if search_task else await search_web(state["message"])
        return {
            "context": format_web_search_context(search_result),
            "sources": web_search_sources(search_result),
        }

    async def emit_web_sources(search_data: dict[str, Any]) -> None:
        sources = search_data.get("sources") or []
        if sources:
            await emit("source_delta", 6, "已获取搜索来源", {"sources": sources})

    async def answer_node(state: AgentState) -> AgentState:
        await emit("progress", 6, "正在整理最终回复", None)
        skill_outputs = state.get("skill_outputs", [])
        skill_output = state.get("skill_output")
        if skill_output is None and not skill_outputs:
            if not llm.available:
                answer = "当前未配置 DEEPSEEK_API_KEY，无法进行普通对话。"
            else:
                web_search_data = await build_web_context(state)
                await emit_web_sources(web_search_data)
                web_context = web_search_data["context"]
                if state.get("web_search"):
                    messages = [
                        {
                            "role": "system",
                            "content": FINAL_ANSWER_SYSTEM_PROMPT,
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "user_message": state["message"],
                                    "history": [
                                        {"role": item.role, "content": item.content}
                                        for item in state.get("history", [])[-12:]
                                    ],
                                    "attachments": attachment_context(state),
                                    "detached_files": [
                                        {"file_id": item.file_id, "filename": item.filename}
                                        for item in state.get("detached_files", [])
                                    ],
                                    "data_profiles": state.get("data_profiles", []),
                                    "web_search": {
                                        "enabled": True,
                                        "context": web_context,
                                        "sources": web_search_data["sources"],
                                    },
                                    "skill_name": None,
                                    "skill_names": [],
                                    "skill_output": None,
                                    "skill_outputs": [],
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                else:
                    messages = [
                        {
                            "role": "system",
                            "content": GENERAL_CHAT_SYSTEM_PROMPT,
                        },
                        {
                            "role": "system",
                            "content": uploaded_files_prompt(state.get("attachments", []))
                            + ("\n" + detached_files_prompt(state) if detached_files_prompt(state) else "")
                            + ("\n" + web_context if web_context else "")
                            + "\n路由判断："
                            + state.get("route_reason", ""),
                        },
                        *llm_history_messages(state),
                    ]
                answer = ""
                async for delta in llm.stream_chat(
                    messages,
                    model=llm.settings.answer_model,
                    temperature=0.4,
                    max_tokens=1200,
                ):
                    answer += delta
                    await emit("answer_delta", 7, "输出中", {"delta": delta})
            return {"answer": answer, "web_sources": web_search_data["sources"] if llm.available else []}

        if not llm.available:
            answer = json.dumps(
                skill_outputs if skill_outputs else skill_output["result"],
                ensure_ascii=False,
                indent=2,
            )
        else:
            web_search_data = await build_web_context(state)
            await emit_web_sources(web_search_data)
            web_context = web_search_data["context"]
            messages = [
                {
                    "role": "system",
                    "content": FINAL_ANSWER_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "user_message": state["message"],
                            "history": [
                                {"role": item.role, "content": item.content}
                                for item in state.get("history", [])[-12:]
                            ],
                            "attachments": attachment_context(state),
                            "detached_files": [
                                {"file_id": item.file_id, "filename": item.filename}
                                for item in state.get("detached_files", [])
                            ],
                            "data_profiles": state.get("data_profiles", []),
                            "web_search": {
                                "enabled": bool(state.get("web_search")),
                                "context": web_context,
                                "sources": web_search_data["sources"],
                            },
                            "skill_name": state["skill_name"],
                            "skill_names": state.get("skill_names", []),
                            "skill_output": answer_ready_output(skill_output),
                            "skill_outputs": [
                                {**item, "output": answer_ready_output(item.get("output"))}
                                if isinstance(item, dict)
                                else compact_value(item)
                                for item in skill_outputs
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            answer = ""
            async for delta in llm.stream_chat(
                messages,
                model=llm.settings.answer_model,
                temperature=0.2,
                max_tokens=1800,
            ):
                answer += delta
                await emit("answer_delta", 7, "输出中", {"delta": delta})
        await emit_ui_blocks(state)
        return {"answer": answer, "web_sources": web_search_data["sources"] if llm.available else []}

    graph.add_node("intake_uploads", intake_uploads_node)
    graph.add_node("load_skills", load_skill_node)
    graph.add_node("route", route_node)
    graph.add_node("execute_skill", execute_node)
    graph.add_node("final_answer", answer_node)
    graph.set_entry_point("intake_uploads")
    graph.add_edge("intake_uploads", "load_skills")
    graph.add_edge("load_skills", "route")
    graph.add_conditional_edges(
        "route",
        lambda state: "execute_skill" if state.get("skill_name") else "final_answer",
        {
            "execute_skill": "execute_skill",
            "final_answer": "final_answer",
        },
    )
    graph.add_edge("execute_skill", "final_answer")
    graph.add_edge("final_answer", END)
    return graph.compile()

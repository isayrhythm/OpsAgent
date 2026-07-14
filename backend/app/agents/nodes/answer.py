from __future__ import annotations

import json
from typing import Any

from backend.app.agents.formatters import (
    attachment_context,
    background_runs_prompt,
    detached_files_prompt,
    format_command_answer,
    llm_history_messages,
)
from backend.app.agents.state import AgentState, Emit
from backend.app.llm.deepseek import DeepSeekClient
from backend.app.llm.prompts import FINAL_ANSWER_SYSTEM_PROMPT, GENERAL_CHAT_SYSTEM_PROMPT


def make_answer_node(llm: DeepSeekClient, emit: Emit, deps: Any):
    async def emit_ui_blocks(state: AgentState) -> None:
        for event in deps.ui_block_events(state.get("skill_outputs", [])):
            await emit("ui_delta", 6, "Rendering research path", event)

    async def build_web_context(state: AgentState) -> dict[str, Any]:
        search_state = state.get("search") if isinstance(state.get("search"), dict) else {}
        if not search_state.get("enabled"):
            return {"context": "", "sources": []}
        await emit("progress", 6, "Collecting Web Search Evidence", {"agent": "Web Search", "agent_state": "running"})
        search_task = search_state.get("task")
        search_plan = search_state.get("plan") or {}
        queries = search_plan.get("queries") if isinstance(search_plan, dict) else None
        search_result = (
            await search_task
            if search_task
            else await deps.search_web_queries(
                queries if isinstance(queries, list) and queries else [{"query": state["message"]}],
                history=state.get("history", []),
                providers=search_state.get("providers", []),
            )
        )
        await emit("progress", 6, "Web Search Completed", {"agent": "Web Search", "agent_state": "done"})
        return {
            "context": deps.format_web_search_context(search_result),
            "sources": deps.web_search_sources(search_result),
            "plan": search_plan,
            "enabled": True,
            "answer_requirements": deps.web_search_answer_requirements,
        }

    async def emit_web_sources(search_data: dict[str, Any]) -> None:
        sources = search_data.get("sources") or []
        if sources:
            await emit("source_delta", 6, "Web Search Sources Ready", {"sources": sources})

    def public_search_state(state: AgentState, search_data: dict[str, Any] | None = None) -> dict[str, Any]:
        search_state = state.get("search") if isinstance(state.get("search"), dict) else {}
        public_state = {key: value for key, value in search_state.items() if key != "task"}
        if search_data:
            public_state["enabled"] = bool(search_data.get("enabled"))
            public_state["sources"] = search_data.get("sources") or []
            public_state["plan"] = search_data.get("plan") or public_state.get("plan")
        return public_state

    async def answer_node(state: AgentState) -> AgentState:
        await emit("progress", 6, "Synthesizing Final Answer", None)
        skill_outputs = state.get("skill_outputs", [])
        skill_output = state.get("skill_output")
        command_outputs = state.get("command_outputs", [])
        background_runs_created = state.get("background_runs_created", [])
        if background_runs_created:
            titles = "、".join(str(item.get("title") or item.get("agent") or "后台任务") for item in background_runs_created)
            answer = (
                f"已启动后台任务：{titles}。任务会继续运行，你现在可以继续聊天；"
                "完成后结果会自动回到当前对话。"
            )
            await emit("answer_delta", 7, "Background Run Started", {"delta": answer})
            search_state = state.get("search") if isinstance(state.get("search"), dict) else {}
            search_task = search_state.get("task")
            if search_task and not search_task.done():
                search_task.cancel()
            return {
                "answer": answer,
                "background_runs_created": background_runs_created,
                "search": public_search_state(state),
            }
        if skill_output is None and not skill_outputs and not command_outputs:
            if not llm.available:
                answer = "当前未配置 DEEPSEEK_API_KEY，无法进行普通对话。"
            else:
                web_search_data = await build_web_context(state)
                await emit_web_sources(web_search_data)
                web_context = web_search_data["context"]
                if web_search_data.get("enabled"):
                    messages = [
                        {"role": "system", "content": FINAL_ANSWER_SYSTEM_PROMPT},
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
                                        "plan": web_search_data.get("plan"),
                                        "answer_requirements": web_search_data.get("answer_requirements", []),
                                    },
                                    "skill_name": None,
                                    "skill_names": [],
                                    "skill_output": None,
                                    "skill_outputs": [],
                                    "command_outputs": [],
                                    "background_runs": state.get("active_runs", []),
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                else:
                    detached_prompt = detached_files_prompt(state)
                    messages = [
                        {"role": "system", "content": GENERAL_CHAT_SYSTEM_PROMPT},
                        {
                            "role": "system",
                            "content": deps.uploaded_files_prompt(state.get("attachments", []))
                            + ("\n" + detached_prompt if detached_prompt else "")
                            + ("\n" + web_context if web_context else "")
                            + ("\n" + background_runs_prompt(state) if state.get("active_runs") else "")
                            + "\n路由判断："
                            + state.get("route_reason", ""),
                        },
                        *llm_history_messages(state),
                    ]
                answer = await deps.complete_text(
                    llm,
                    messages,
                    model=llm.settings.answer_model,
                    temperature=0.4,
                    max_tokens=1200,
                    emit_delta=lambda delta: emit("answer_delta", 7, "Streaming Answer", {"delta": delta}),
                )
            return {
                "answer": answer,
                "web_sources": web_search_data["sources"] if llm.available else [],
                "search": public_search_state(state, web_search_data if llm.available else None),
            }

        if not llm.available:
            if command_outputs and skill_output is None and not skill_outputs:
                answer = format_command_answer(command_outputs)
            else:
                answer = json.dumps(
                    skill_outputs if skill_outputs else skill_output["result"],
                    ensure_ascii=False,
                    indent=2,
                )
        else:
            web_search_data = await build_web_context(state)
            await emit_web_sources(web_search_data)
            web_context = web_search_data["context"]
            skills_by_name = {skill.name: skill for skill in state.get("skills", [])}
            messages = [
                {"role": "system", "content": FINAL_ANSWER_SYSTEM_PROMPT},
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
                                "enabled": bool(web_search_data.get("enabled")),
                                "context": web_context,
                                "sources": web_search_data["sources"],
                                "plan": web_search_data.get("plan"),
                                "answer_requirements": web_search_data.get("answer_requirements", []),
                            },
                            "skill_name": state.get("skill_name"),
                            "skill_names": state.get("skill_names", []),
                            "skill_output": deps.answer_ready_output(
                                skill_output,
                                skills_by_name.get(str(state.get("skill_name") or "")),
                            ),
                            "skill_outputs": [
                                {
                                    **item,
                                    "output": deps.answer_ready_output(
                                        item.get("output"),
                                        skills_by_name.get(str(item.get("skill_name") or "")),
                                    ),
                                }
                                if isinstance(item, dict)
                                else deps.compact_value(item)
                                for item in skill_outputs
                            ],
                            "command_outputs": deps.compact_value(command_outputs),
                            "background_runs": state.get("active_runs", []),
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            answer = await deps.complete_text(
                llm,
                messages,
                model=llm.settings.answer_model,
                temperature=0.2,
                max_tokens=1800,
                emit_delta=lambda delta: emit("answer_delta", 7, "Streaming Answer", {"delta": delta}),
            )
        await emit_ui_blocks(state)
        return {
            "answer": answer,
            "web_sources": web_search_data["sources"] if llm.available else [],
            "search": public_search_state(state, web_search_data if llm.available else None),
        }

    return answer_node

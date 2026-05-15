from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, TypedDict

from langgraph.graph import END, StateGraph

from backend.app.llm.prompts import FINAL_ANSWER_SYSTEM_PROMPT, GENERAL_CHAT_SYSTEM_PROMPT
from backend.app.schemas import ChatHistoryMessage
from backend.app.services.code_executor import execute_skill
from backend.app.services.deepseek_client import DeepSeekClient
from backend.app.services.router import route_skill
from backend.app.services.skill_loader import SkillSpec, load_skill, load_skill_catalog


Emit = Callable[[str, int, str, Any | None], Awaitable[None]]


class AgentState(TypedDict, total=False):
    message: str
    history: list[ChatHistoryMessage]
    skills: list[SkillSpec]
    skill_name: str | None
    skill_output: dict[str, Any]
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

    async def load_skill_node(state: AgentState) -> AgentState:
        await emit("progress", 1, "正在理解请求", None)
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
        skill = await route_skill(state["message"], skills, llm)
        if skill is None:
            await emit("progress", 4, "使用普通对话模式", None)
            return {"skill_name": None, "skills": []}
        await emit(
            "progress",
            4,
            "正在调用专门能力",
            None,
        )
        return {"skill_name": skill.name, "skills": [load_skill(skill.path)]}

    async def execute_node(state: AgentState) -> AgentState:
        await emit("progress", 5, "正在处理数据", None)
        skill = state["skills"][0]
        skill_output = await execute_skill(state["message"], skill, llm)
        return {"skill_output": skill_output}

    async def answer_node(state: AgentState) -> AgentState:
        await emit("progress", 6, "正在整理最终回复", None)
        skill_output = state.get("skill_output")
        if skill_output is None:
            if not llm.available:
                answer = "当前未配置 DEEPSEEK_API_KEY，无法进行普通对话。"
            else:
                messages = [
                    {
                        "role": "system",
                        "content": GENERAL_CHAT_SYSTEM_PROMPT,
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
            return {"answer": answer}

        if not llm.available:
            answer = json.dumps(skill_output["result"], ensure_ascii=False, indent=2)
        else:
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
                            "skill_name": state["skill_name"],
                            "skill_output": skill_output,
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
                max_tokens=1000,
            ):
                answer += delta
                await emit("answer_delta", 7, "输出中", {"delta": delta})
        return {"answer": answer}

    graph.add_node("load_skills", load_skill_node)
    graph.add_node("route", route_node)
    graph.add_node("execute_skill", execute_node)
    graph.add_node("final_answer", answer_node)
    graph.set_entry_point("load_skills")
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

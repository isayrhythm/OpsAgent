from __future__ import annotations

import json
import re
from dataclasses import dataclass

from backend.app.schemas import ChatHistoryMessage
from backend.app.services.deepseek_client import DeepSeekClient
from backend.app.llm.prompts import ROUTER_SYSTEM_PROMPT
from backend.app.services.skill_loader import SkillSpec


FOLLOW_UP_MARKERS = (
    "这个",
    "该",
    "它",
    "上一个",
    "刚才",
    "上述",
    "前面",
    "其中",
    "什么",
    "哪些",
    "为什么",
    "怎么",
    "表达证据",
    "功能呢",
    "呢",
)


@dataclass(frozen=True)
class RouteDecision:
    skill: SkillSpec | None
    resolved_message: str


def _json_from_text(text: str) -> dict[str, object]:
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        text = match.group(0)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("router response must be a JSON object")
    return value


def _fallback_resolve_message(message: str, history: list[ChatHistoryMessage]) -> str:
    message = message.strip()
    is_follow_up = bool(history) and (
        len(message) <= 40 or any(marker in message for marker in FOLLOW_UP_MARKERS)
    )
    if not is_follow_up:
        return message

    recent = history[-6:]
    context = "\n".join(f"{item.role}: {item.content}" for item in recent)
    return (
        "以下是最近对话上下文，仅用于补全当前问题中省略的基因 ID、物种或查询对象；"
        "必须回答当前用户问题。\n"
        f"{context}\n"
        f"当前用户问题: {message}"
    )


def _fallback_skill(message: str, skills: list[SkillSpec]) -> SkillSpec | None:
    normalized = message.lower()
    gene_id_pattern = re.compile(
        r"(loc_os\d+g\d+|agis_os\d+g\d+|zm\d+[a-z]*\d+|glyma\.\d+g\d+|gmw82\.\d+g\d+)",
        re.I,
    )
    gene_info_query = bool(gene_id_pattern.search(message)) and any(
        token in normalized
        for token in (
            "基因",
            "信息",
            "注释",
            "功能",
            "位置",
            "长度",
            "证据",
            "表达",
            "gene",
            "annotation",
            "function",
            "evidence",
        )
    )
    if gene_info_query:
        gene_info_skill = next((skill for skill in skills if skill.name == "query_gene_info"), None)
        if gene_info_skill is not None:
            return gene_info_skill

    for skill in skills:
        if skill.name.lower() in normalized:
            return skill
        if skill.name == "query_gene_expression" and any(
            token in message for token in ("基因", "表达", "拟南芥", "gene", "expression")
        ):
            return skill
    return None


async def route_skill(
    message: str,
    skills: list[SkillSpec],
    llm: DeepSeekClient,
    history: list[ChatHistoryMessage] | None = None,
) -> RouteDecision:
    history = history or []
    if not skills:
        return RouteDecision(skill=None, resolved_message=message)

    if not llm.available:
        resolved_message = _fallback_resolve_message(message, history)
        return RouteDecision(skill=_fallback_skill(resolved_message, skills), resolved_message=resolved_message)

    catalog = [
        {
            "name": skill.name,
            "description": skill.description,
            "trigger": skill.trigger,
            "execution_mode": skill.execution_mode,
            "data_paths": skill.data_paths,
        }
        for skill in skills
    ]
    response = await llm.chat(
        [
            {
                "role": "system",
                "content": ROUTER_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "current_message": message,
                        "history": [
                            {"role": item.role, "content": item.content}
                            for item in history[-8:]
                        ],
                        "skills": catalog,
                        "output_schema": {
                            "depends_on_history": "boolean",
                            "resolved_message": "string",
                            "skill_name": "string or null",
                            "reason": "string",
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        model=llm.settings.router_model,
        temperature=0,
        max_tokens=500,
    )
    try:
        routed = _json_from_text(response)
    except Exception:
        resolved_message = _fallback_resolve_message(message, history)
        return RouteDecision(skill=_fallback_skill(resolved_message, skills), resolved_message=resolved_message)

    resolved_message = routed.get("resolved_message")
    if not isinstance(resolved_message, str) or not resolved_message.strip():
        resolved_message = message
    resolved_message = resolved_message.strip()
    skill_name = routed.get("skill_name")
    if not isinstance(skill_name, str):
        return RouteDecision(skill=_fallback_skill(resolved_message, skills), resolved_message=resolved_message)
    skill = next((skill for skill in skills if skill.name == skill_name), None)
    deterministic_skill = _fallback_skill(resolved_message, skills)
    if deterministic_skill is not None and deterministic_skill.name == "query_gene_info":
        skill = deterministic_skill
    elif skill is None:
        skill = deterministic_skill
    return RouteDecision(skill=skill, resolved_message=resolved_message)

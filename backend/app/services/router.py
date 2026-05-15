from __future__ import annotations

import json
import re

from backend.app.services.deepseek_client import DeepSeekClient
from backend.app.llm.prompts import ROUTER_SYSTEM_PROMPT
from backend.app.services.skill_loader import SkillSpec


def _json_from_text(text: str) -> dict[str, object]:
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        text = match.group(0)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("router response must be a JSON object")
    return value


def _fallback_route(message: str, skills: list[SkillSpec]) -> SkillSpec | None:
    normalized = message.lower()
    for skill in skills:
        if skill.name.lower() in normalized:
            return skill
        if skill.name == "query_gene_expression" and any(
            token in message for token in ("基因", "表达", "拟南芥", "gene", "expression")
        ):
            return skill
    return None


async def route_skill(message: str, skills: list[SkillSpec], llm: DeepSeekClient) -> SkillSpec | None:
    if not skills:
        return None

    if not llm.available:
        return _fallback_route(message, skills)

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
                        "user_message": message,
                        "skills": catalog,
                        "output_schema": {"skill_name": "string or null", "reason": "string"},
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
        return _fallback_route(message, skills)

    skill_name = routed.get("skill_name")
    if not isinstance(skill_name, str):
        return None
    return next((skill for skill in skills if skill.name == skill_name), None)

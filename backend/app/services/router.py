from __future__ import annotations

import json
import re
from dataclasses import dataclass

from backend.app.llm.calls import chat_json
from backend.app.llm.prompts import ROUTER_SYSTEM_PROMPT
from backend.app.schemas import ChatHistoryMessage, DetachedFileSummary
from backend.app.services.deepseek_client import DeepSeekClient
from backend.app.services.skill_loader import SkillSpec


SEQUENCE_SYMBOLS = set("ACGTUNRYKMSWBDHVACDEFGHIKLMNPQRSTVWYBXZJUO*")
LONG_SEQUENCE_RE = re.compile(r"[A-Za-z*]{80,}")


@dataclass(frozen=True)
class RouteDecision:
    skill: SkillSpec | None
    skills: list[SkillSpec]
    reason: str


def _dedupe_skills(skills: list[SkillSpec]) -> list[SkillSpec]:
    seen: set[str] = set()
    result: list[SkillSpec] = []
    for skill in skills:
        if skill.name in seen:
            continue
        seen.add(skill.name)
        result.append(skill)
    return result


def _recent_focus(history: list[ChatHistoryMessage]) -> dict[str, str]:
    focus = {"last_user_message": "", "last_assistant_message": ""}
    for item in reversed(history):
        content = _compact_sequence_text(str(item.content or "").strip())
        if not content:
            continue
        if item.role == "user" and not focus["last_user_message"]:
            focus["last_user_message"] = content
        elif item.role in {"assistant", "agent"} and not focus["last_assistant_message"]:
            focus["last_assistant_message"] = content
        if focus["last_user_message"] and focus["last_assistant_message"]:
            break
    return focus


def _compact_sequence_text(text: str) -> str:
    compacted_lines = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        sequence = re.sub(r"\s+", "", stripped)
        if len(sequence) >= 20 and set(sequence.upper()) <= SEQUENCE_SYMBOLS:
            compacted_lines.append(f"[sequence omitted: {len(sequence)} residues]")
        else:
            compacted_lines.append(line)
    compacted = "\n".join(compacted_lines)

    def replace_inline(match: re.Match[str]) -> str:
        value = match.group(0)
        if set(value.upper()) <= SEQUENCE_SYMBOLS:
            return f"[sequence omitted: {len(value)} residues]"
        return value

    return LONG_SEQUENCE_RE.sub(replace_inline, compacted)


async def route_skill(
    message: str,
    skills: list[SkillSpec],
    llm: DeepSeekClient,
    history: list[ChatHistoryMessage] | None = None,
    data_profiles: list[dict[str, object]] | None = None,
    detached_files: list[DetachedFileSummary] | None = None,
) -> RouteDecision:
    history = history or []
    if not skills:
        return RouteDecision(skill=None, skills=[], reason="No skills available")
    if not llm.available:
        raise RuntimeError("DeepSeek router model is unavailable; cannot route request")

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
    try:
        routed = await chat_json(
            llm,
            [
                {
                    "role": "system",
                    "content": ROUTER_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "current_message": _compact_sequence_text(message),
                            "recent_focus": _recent_focus(history),
                            "history": [
                                {"role": item.role, "content": _compact_sequence_text(item.content)}
                                for item in history[-8:]
                            ],
                            "data_profiles": data_profiles or [],
                            "detached_files": [
                                {"file_id": item.file_id, "filename": item.filename}
                                for item in (detached_files or [])
                            ],
                            "skills": catalog,
                            "output_schema": {
                                "skill_names": ["string"],
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
    except Exception as exc:
        raise RuntimeError("DeepSeek router returned invalid JSON") from exc

    skill_names = routed.get("skill_names")
    if not isinstance(skill_names, list):
        skill_name = routed.get("skill_name")
        skill_names = [skill_name] if isinstance(skill_name, str) else []
    reason = routed.get("reason")
    if not isinstance(reason, str):
        reason = ""

    selected = [
        skill
        for name in skill_names
        if isinstance(name, str)
        for skill in skills
        if skill.name == name
    ]
    selected = _dedupe_skills(selected)
    return RouteDecision(skill=selected[0] if selected else None, skills=selected, reason=reason)

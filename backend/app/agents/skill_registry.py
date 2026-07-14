from __future__ import annotations

from typing import Any

from backend.app.schemas import ChatHistoryMessage, DetachedFileSummary
from backend.app.llm.deepseek import DeepSeekClient
from backend.app.services.router import route_skill
from backend.app.services.skill_loader import SkillSpec, load_skill, load_skill_catalog


def load_skill_registry() -> list[SkillSpec]:
    return load_skill_catalog()


def _load_selected_skill(skill: SkillSpec) -> SkillSpec:
    if skill.execution_mode == "builtin_tool":
        return skill
    return load_skill(skill.path)


async def route_registered_skills(
    *,
    message: str,
    skills: list[SkillSpec],
    llm: DeepSeekClient,
    history: list[ChatHistoryMessage] | None = None,
    data_profiles: list[dict[str, Any]] | None = None,
    detached_files: list[DetachedFileSummary] | None = None,
    active_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    decision = await route_skill(
        message,
        skills,
        llm,
        history or [],
        data_profiles or [],
        detached_files or [],
        active_runs or [],
    )
    selected_skills = decision.skills
    if not selected_skills:
        return {
            "skill_name": None,
            "skill_names": [],
            "skills": [],
            "route_reason": decision.reason,
        }

    loaded_skills = [_load_selected_skill(skill) for skill in selected_skills]
    return {
        "skill_name": loaded_skills[0].name,
        "skill_names": [skill.name for skill in loaded_skills],
        "skills": loaded_skills,
        "route_reason": decision.reason,
    }

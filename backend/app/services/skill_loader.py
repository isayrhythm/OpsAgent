from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.app.config import SKILL_DIR


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    path: Path
    content: str


def _parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    values: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _skill_from_path(path: Path, *, include_content: bool) -> SkillSpec | None:
    content = path.read_text(encoding="utf-8")
    meta = _parse_frontmatter(content)
    name = meta.get("name", path.stem).strip()
    description = meta.get("description", "").strip()
    if not name:
        return None
    return SkillSpec(
        name=name,
        description=description,
        path=path,
        content=content if include_content else "",
    )


def load_skill_catalog(skill_dir: Path = SKILL_DIR) -> list[SkillSpec]:
    if not skill_dir.exists():
        return []

    skills: list[SkillSpec] = []
    for path in sorted(skill_dir.glob("*.md")):
        skill = _skill_from_path(path, include_content=False)
        if skill is not None:
            skills.append(skill)
    return skills


def load_skill(path: Path) -> SkillSpec:
    skill = _skill_from_path(path, include_content=True)
    if skill is None:
        raise ValueError(f"Invalid skill file: {path}")
    return skill


def load_skills(skill_dir: Path = SKILL_DIR) -> list[SkillSpec]:
    if not skill_dir.exists():
        return []

    skills: list[SkillSpec] = []
    for path in sorted(skill_dir.glob("*.md")):
        skill = _skill_from_path(path, include_content=True)
        if skill is not None:
            skills.append(skill)
    return skills

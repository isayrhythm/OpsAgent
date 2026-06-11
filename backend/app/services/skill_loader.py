from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.config import PROJECT_ROOT, SKILL_DIR


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    version: str
    trigger: str
    execution_mode: str
    data_paths: list[str]
    path: Path
    content: str
    executor: str = ""
    argument_resolver: str = ""
    input_schema_path: str = ""
    output_schema_path: str = ""
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    answer_requirements: list[str] | None = None


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


def _parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_requirement_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(";") if item.strip()]


def _load_schema(value: str) -> tuple[str, dict[str, Any] | None]:
    schema_path = value.strip()
    if not schema_path:
        return "", None
    path = Path(schema_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Skill schema must be a JSON object: {path}")
    return schema_path, payload


def _skill_from_path(path: Path, *, include_content: bool) -> SkillSpec | None:
    content = path.read_text(encoding="utf-8")
    meta = _parse_frontmatter(content)
    name = meta.get("name", path.stem).strip()
    description = meta.get("description", "").strip()
    if not name:
        return None
    input_schema_path, input_schema = _load_schema(meta.get("input_schema", ""))
    output_schema_path, output_schema = _load_schema(meta.get("output_schema", ""))
    return SkillSpec(
        name=name,
        description=description,
        version=meta.get("version", "1").strip(),
        trigger=meta.get("trigger", description).strip(),
        execution_mode=meta.get("execution_mode", "generated_python").strip(),
        data_paths=_parse_csv_list(meta.get("data_paths", "")),
        executor=meta.get("executor", "").strip(),
        argument_resolver=meta.get("argument_resolver", "").strip(),
        input_schema_path=input_schema_path,
        output_schema_path=output_schema_path,
        input_schema=input_schema,
        output_schema=output_schema,
        answer_requirements=_parse_requirement_list(meta.get("answer_requirements", "")),
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

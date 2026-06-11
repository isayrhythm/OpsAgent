from __future__ import annotations

from typing import Any, Iterator

from backend.app.services.id_mapping import with_id_mapping_summary
from backend.app.services.result_evaluator import compact_value
from backend.app.services.skill_loader import SkillSpec


def ui_block_events(skill_outputs: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for item in skill_outputs:
        result = item.get("output", {}).get("result")
        if not isinstance(result, dict):
            continue
        for block in result.get("ui_blocks", []):
            if not isinstance(block, dict) or block.get("type") != "gene_function_research_path":
                continue
            block_header = {key: value for key, value in block.items() if key != "steps"}
            yield {"action": "start", "block": block_header}
            for step in block.get("steps", []):
                if isinstance(step, dict):
                    yield {"action": "step", "block_id": block.get("id"), "step": step}


def answer_ready_output(value: Any, skill: SkillSpec | None = None) -> Any:
    if not isinstance(value, dict):
        return compact_value(value)

    result = value.get("result")
    result = with_id_mapping_summary(result)
    result = _apply_answer_requirements(result, skill)
    result = _summarize_ui_blocks(result)
    output = {**value, "result": result} if result is not value.get("result") else value
    return compact_value(output)


def _apply_answer_requirements(result: Any, skill: SkillSpec | None) -> Any:
    requirements = skill.answer_requirements if skill else None
    if not requirements or not isinstance(result, dict):
        return result
    return {**result, "answer_requirements": requirements}


def _summarize_ui_blocks(result: Any) -> Any:
    if not isinstance(result, dict) or not result.get("ui_blocks"):
        return result

    summarized = {key: item for key, item in result.items() if key != "ui_blocks"}
    if isinstance(result.get("matches"), list):
        summarized["matches"] = [
            {
                "paper_id": match.get("paper_id"),
                "title": match.get("title"),
                "gene_id": match.get("gene_id"),
                "step_count": len(match.get("steps", [])),
            }
            for match in result.get("matches", [])
            if isinstance(match, dict)
        ]
    summarized["visualized_ui_blocks"] = [
        {
            "type": block.get("type"),
            "gene_id": block.get("gene_id"),
            "title": block.get("title"),
            "step_count": len(block.get("steps", [])),
        }
        for block in result.get("ui_blocks", [])
        if isinstance(block, dict)
    ]
    return summarized

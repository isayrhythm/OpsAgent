from __future__ import annotations

from typing import Any


SKILL_LABELS = {
    "differential_protein_analysis": "差异蛋白组分析",
    "differential_transcriptomics_analysis": "差异转录组分析",
}


def format_background_skill_answer(skill_name: str, skill_output: dict[str, Any]) -> str:
    label = SKILL_LABELS.get(skill_name, skill_name)
    result = skill_output.get("result") if isinstance(skill_output, dict) else None
    if not isinstance(result, dict):
        return f"后台任务“{label}”已结束，但没有返回可用的结构化结果。"
    if result.get("error"):
        return f"后台任务“{label}”未能完成：{result['error']}"

    lines = [f"后台任务“{label}”已完成。"]
    parameters = result.get("parameters") if isinstance(result.get("parameters"), dict) else {}
    if parameters:
        values = ", ".join(f"{key}={value}" for key, value in parameters.items())
        lines.append(f"分析参数：`{values}`。")

    comparisons = result.get("comparisons") if isinstance(result.get("comparisons"), list) else []
    if comparisons:
        lines.append("")
        lines.append("比较结果：")
        for item in comparisons:
            if not isinstance(item, dict):
                continue
            name = item.get("comparison") or item.get("slug") or "comparison"
            if skill_name == "differential_protein_analysis":
                counts = _count_summary(item, ["differential", "up", "down"])
            else:
                counts = _count_summary(item, ["significant", "up", "down"])
            lines.append(f"- {name}：{counts}" if counts else f"- {name}")

    files = result.get("files") if isinstance(result.get("files"), dict) else {}
    report_url = files.get("report_url")
    if report_url:
        lines.extend(["", f"[打开 HTML report]({report_url})"])
    elif files:
        lines.extend(["", "HTML report 未生成。可用输出："])
        for key, value in files.items():
            if value:
                lines.append(f"- `{key}`: `{value}`")

    workflow = result.get("workflow") if isinstance(result.get("workflow"), dict) else {}
    quality = workflow.get("quality_control") if isinstance(workflow.get("quality_control"), dict) else {}
    warnings = quality.get("warnings") if isinstance(quality.get("warnings"), list) else []
    if warnings:
        lines.extend(["", "质控提示：" + "；".join(str(item) for item in warnings)])
    return "\n".join(lines)


def background_run_title(skill_name: str) -> str:
    return SKILL_LABELS.get(skill_name, skill_name)


def background_run_artifacts(skill_output: dict[str, Any]) -> dict[str, Any]:
    result = skill_output.get("result") if isinstance(skill_output, dict) else None
    if not isinstance(result, dict):
        return {}
    files = result.get("files")
    return files if isinstance(files, dict) else {}


def _count_summary(value: dict[str, Any], keys: list[str]) -> str:
    parts = [f"{key}={value[key]}" for key in keys if value.get(key) is not None]
    if value.get("total") is not None:
        parts.insert(0, f"total={value['total']}")
    return ", ".join(parts)

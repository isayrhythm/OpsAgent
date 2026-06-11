from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from backend.app.llm.calls import chat_json
from backend.app.llm.prompts import DETERMINISTIC_ANALYSIS_ARGUMENTS_SYSTEM_PROMPT
from backend.app.services.deepseek_client import DeepSeekClient


Emit = Callable[[str, int, str, Any | None], Awaitable[None]]


def _available_groups(data_profiles: list[dict[str, Any]], data_family: str) -> list[str]:
    groups: list[str] = []
    for profile in data_profiles:
        if (
            profile.get("status") != "ready"
            or profile.get("analysis_ready") is not True
            or profile.get("confidence") != "high"
            or profile.get("data_family") != data_family
            or profile.get("data_type") != "expression_matrix"
        ):
            continue
        for group in (profile.get("sample_groups") or {}):
            name = str(group)
            if name not in groups:
                groups.append(name)
    return groups


def _number(value: Any, *, minimum: float, maximum: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < minimum or (maximum is not None and number >= maximum):
        return None
    return number


def _group(value: Any, available: set[str]) -> str | None:
    name = str(value or "")
    return name if name in available else None


def _protein_arguments(value: dict[str, Any], groups: list[str]) -> dict[str, Any]:
    available = set(groups)
    comparisons: list[dict[str, str]] = []
    for item in value.get("comparisons") or []:
        if not isinstance(item, dict):
            continue
        numerator = _group(item.get("numerator"), available)
        denominator = _group(item.get("denominator"), available)
        if not numerator or not denominator or numerator == denominator:
            continue
        comparison = {"numerator": numerator, "denominator": denominator}
        if comparison not in comparisons:
            comparisons.append(comparison)
    return {
        "comparisons": comparisons,
        "pvalue_cutoff": _number(value.get("pvalue_cutoff"), minimum=0, maximum=1),
        "fold_change_cutoff": _number(value.get("fold_change_cutoff"), minimum=1),
        "reason": str(value.get("reason") or ""),
    }


def _transcriptomics_arguments(value: dict[str, Any], groups: list[str]) -> dict[str, Any]:
    available = set(groups)
    comparisons: list[dict[str, str]] = []
    for item in value.get("comparisons") or []:
        if not isinstance(item, dict):
            continue
        numerator = _group(item.get("numerator"), available)
        denominator = _group(item.get("denominator"), available)
        if not numerator or not denominator or numerator == denominator:
            continue
        comparison = {"numerator": numerator, "denominator": denominator}
        if comparison not in comparisons:
            comparisons.append(comparison)
    return {
        "comparisons": comparisons,
        "padj_cutoff": _number(value.get("padj_cutoff"), minimum=0, maximum=1),
        "log2_fc_cutoff": _number(value.get("log2_fc_cutoff"), minimum=0),
        "reason": str(value.get("reason") or ""),
    }


def default_differential_arguments(skill_name: str) -> dict[str, Any]:
    if skill_name == "differential_protein_analysis":
        return {
            "comparisons": [],
            "pvalue_cutoff": None,
            "fold_change_cutoff": None,
            "reason": "使用执行器默认参数。",
        }
    if skill_name == "differential_transcriptomics_analysis":
        return {
            "comparisons": [],
            "padj_cutoff": None,
            "log2_fc_cutoff": None,
            "reason": "使用执行器默认参数。",
        }
    return {}


async def resolve_differential_arguments(
    message: str,
    skill_name: str,
    data_profiles: list[dict[str, Any]],
    llm: DeepSeekClient,
    emit: Emit | None = None,
) -> dict[str, Any]:
    arguments = default_differential_arguments(skill_name)
    if skill_name == "differential_protein_analysis":
        data_family = "proteomics"
        output_schema: dict[str, Any] = {
            "comparisons": [{"numerator": "string", "denominator": "string"}],
            "pvalue_cutoff": "number|null",
            "fold_change_cutoff": "number|null",
            "reason": "string",
        }
        sanitize = _protein_arguments
    elif skill_name == "differential_transcriptomics_analysis":
        data_family = "transcriptomics"
        output_schema = {
            "comparisons": [{"numerator": "string", "denominator": "string"}],
            "padj_cutoff": "number|null",
            "log2_fc_cutoff": "number|null",
            "reason": "string",
        }
        sanitize = _transcriptomics_arguments
    else:
        return arguments

    groups = _available_groups(data_profiles, data_family)
    if not llm.available:
        return arguments

    try:
        response = await chat_json(
            llm,
            [
                {"role": "system", "content": DETERMINISTIC_ANALYSIS_ARGUMENTS_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "current_message": message,
                            "skill_name": skill_name,
                            "available_groups": groups,
                            "output_schema": output_schema,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            model=llm.settings.router_model,
            temperature=0,
            max_tokens=500,
        )
        if emit is not None:
            await emit(
                "thinking_delta",
                5,
                f"正在解析 {skill_name} 调用参数",
                {"delta_length": len(json.dumps(response, ensure_ascii=False))},
            )
        return sanitize(response, groups)
    except Exception:
        return arguments

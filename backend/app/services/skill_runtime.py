from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from backend.app.schemas import UploadedFileSummary
from backend.app.services.deepseek_client import DeepSeekClient
from backend.app.services.differential_arguments import resolve_differential_arguments
from backend.app.services.differential_protein import run_differential_protein_analysis
from backend.app.services.differential_transcriptomics import run_differential_transcriptomics_analysis
from backend.app.services.gene_function_research_path import run_gene_function_research_path_query
from backend.app.services.skill_loader import SkillSpec


Emit = Callable[[str, int, str, Any | None], Awaitable[None]]


class SkillContractError(ValueError):
    pass


@dataclass(frozen=True)
class SkillInvocation:
    skill_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class SkillExecutionContext:
    message: str
    attachments: list[UploadedFileSummary]
    data_profiles: list[dict[str, Any]]
    llm: DeepSeekClient
    emit: Emit | None = None


@dataclass(frozen=True)
class SkillExecutorBinding:
    name: str
    mode: str
    run: Callable[[SkillInvocation, SkillExecutionContext], Awaitable[dict[str, Any]]]


async def execute_registered_skill(skill: SkillSpec, context: SkillExecutionContext) -> dict[str, Any]:
    binding = SKILL_EXECUTORS.get(skill.executor)
    if binding is None:
        raise SkillContractError(f"Skill executor is not registered: {skill.executor or skill.name}")
    invocation = await _resolve_invocation(skill, context)
    _validate_contract(invocation.arguments, skill.input_schema, f"{skill.name} input")
    result = await binding.run(invocation, context)
    _validate_contract(result, skill.output_schema, f"{skill.name} output")
    return {
        "mode": binding.mode,
        "invocation": {
            "skill_name": invocation.skill_name,
            "arguments": invocation.arguments,
        },
        "result": result,
    }


async def _resolve_invocation(skill: SkillSpec, context: SkillExecutionContext) -> SkillInvocation:
    if skill.argument_resolver == "message":
        arguments = {"message": context.message}
    elif skill.argument_resolver == "differential_analysis_json":
        if context.emit is not None:
            await context.emit(
                "progress",
                5,
                f"正在解析 {skill.name} 调用参数",
                {"agent": skill.name, "agent_state": "running"},
            )
        arguments = await resolve_differential_arguments(
            context.message,
            skill.name,
            context.data_profiles,
            context.llm,
            context.emit,
        )
    elif skill.argument_resolver:
        raise SkillContractError(f"Skill argument resolver is not registered: {skill.argument_resolver}")
    else:
        arguments = {}
    return SkillInvocation(skill_name=skill.name, arguments=arguments)


async def _run_gene_function_research_path(
    invocation: SkillInvocation,
    _context: SkillExecutionContext,
) -> dict[str, Any]:
    return await asyncio.to_thread(run_gene_function_research_path_query, invocation.arguments["message"])


async def _run_differential_protein(
    invocation: SkillInvocation,
    context: SkillExecutionContext,
) -> dict[str, Any]:
    rejected = _reject_mismatched_matrix(
        context.data_profiles,
        "proteomics",
        "上传文件尚未被高置信识别为可分析的蛋白组表达矩阵，不能调用蛋白差异分析。",
    )
    if rejected is not None:
        return rejected
    return await asyncio.to_thread(run_differential_protein_analysis, context.attachments, invocation.arguments)


async def _run_differential_transcriptomics(
    invocation: SkillInvocation,
    context: SkillExecutionContext,
) -> dict[str, Any]:
    rejected = _reject_mismatched_matrix(
        context.data_profiles,
        "transcriptomics",
        "上传文件尚未被高置信识别为可分析的转录组 counts 表达矩阵，不能调用转录组差异分析。",
    )
    if rejected is not None:
        return rejected
    return await asyncio.to_thread(run_differential_transcriptomics_analysis, context.attachments, invocation.arguments)


def _reject_mismatched_matrix(
    data_profiles: list[dict[str, Any]],
    data_family: str,
    error: str,
) -> dict[str, Any] | None:
    ready = any(
        profile.get("status") == "ready"
        and profile.get("analysis_ready") is True
        and profile.get("confidence") == "high"
        and profile.get("data_family") == data_family
        and profile.get("data_type") == "expression_matrix"
        for profile in data_profiles
    )
    if data_profiles and not ready:
        return {"error": error, "data_profiles": data_profiles}
    return None


def _validate_contract(value: Any, schema: dict[str, Any] | None, label: str) -> None:
    if schema is None:
        return
    _validate_value(value, schema, label)


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> None:
    variants = schema.get("anyOf")
    if isinstance(variants, list):
        errors = []
        for variant in variants:
            try:
                _validate_value(value, variant, path)
                return
            except SkillContractError as exc:
                errors.append(str(exc))
        raise SkillContractError(f"{path} does not match any contract variant: {'; '.join(errors[:3])}")

    expected = schema.get("type")
    if expected is not None and not _matches_type(value, expected):
        raise SkillContractError(f"{path} must be {_type_label(expected)}")

    minimum = schema.get("minimum")
    exclusive_maximum = schema.get("exclusiveMaximum")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(minimum, (int, float)) and value < minimum:
            raise SkillContractError(f"{path} must be >= {minimum}")
        if isinstance(exclusive_maximum, (int, float)) and value >= exclusive_maximum:
            raise SkillContractError(f"{path} must be < {exclusive_maximum}")

    if isinstance(value, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            raise SkillContractError(f"{path} must contain at least {min_length} characters")

    if isinstance(value, dict):
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                raise SkillContractError(f"{path}.{key} is required")
        properties = schema.get("properties") or {}
        for key, item in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, dict):
                _validate_value(item, child_schema, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise SkillContractError(f"{path}.{key} is not allowed")

    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate_value(item, items, f"{path}[{index}]")


def _matches_type(value: Any, expected: Any) -> bool:
    expected_types = expected if isinstance(expected, list) else [expected]
    return any(_matches_single_type(value, item) for item in expected_types)


def _matches_single_type(value: Any, expected: Any) -> bool:
    return {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
        "null": lambda: value is None,
    }.get(str(expected), lambda: False)()


def _type_label(expected: Any) -> str:
    if isinstance(expected, list):
        return " or ".join(str(item) for item in expected)
    return str(expected)


SKILL_EXECUTORS = {
    "gene_function_research_path_query": SkillExecutorBinding(
        name="gene_function_research_path_query",
        mode="deterministic_query",
        run=_run_gene_function_research_path,
    ),
    "differential_protein_analysis": SkillExecutorBinding(
        name="differential_protein_analysis",
        mode="deterministic_analysis",
        run=_run_differential_protein,
    ),
    "differential_transcriptomics_analysis": SkillExecutorBinding(
        name="differential_transcriptomics_analysis",
        mode="deterministic_analysis",
        run=_run_differential_transcriptomics,
    ),
}

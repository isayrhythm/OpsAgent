from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from backend.app.agents.omics_analysis import run_omics_analysis_graph
from backend.app.schemas import ChatHistoryMessage, UploadedFileSummary
from backend.app.skill_tools.blast_query import classify_blast_query, run_blast_query
from backend.app.llm.deepseek import DeepSeekClient
from backend.app.services.message_context import build_skill_message_with_context
from backend.app.tools.file_context import profile_uploaded_files, transform_attachments_for_skill
from backend.app.skill_tools.differential_arguments import resolve_differential_arguments
from backend.app.skill_tools.differential_protein import run_differential_protein_analysis
from backend.app.skill_tools.differential_transcriptomics import run_differential_transcriptomics_analysis
from backend.app.skill_tools.gene_function_research_path import run_gene_function_research_path_query
from backend.app.skill_tools.gene_info_lookup import run_gene_info_query
from backend.app.skill_tools.gene_mutant_query import run_gene_mutant_query
from backend.app.skill_tools.gene_phenotype_prediction import run_gene_phenotype_prediction
from backend.app.skill_tools.primer_query import classify_primer_query, run_primer_query
from backend.app.services.skill_loader import SkillSpec
from backend.app.skill_tools.trait2gene import classify_trait2gene_query, run_trait2gene_query


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
    history: list[ChatHistoryMessage]
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
    context = await _prepare_context_for_skill(skill, context)
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


async def _prepare_context_for_skill(skill: SkillSpec, context: SkillExecutionContext) -> SkillExecutionContext:
    # File Transformer：只有具体 skill 被选中后，才把通用文件上下文转换成该 skill 的输入结构。
    prepared_attachments = await transform_attachments_for_skill(context.attachments, skill, context.llm)
    if prepared_attachments is context.attachments:
        return context
    prepared_profiles = await asyncio.to_thread(profile_uploaded_files, prepared_attachments)
    if context.emit is not None:
        filenames = [item.filename for item in context.attachments]
        await context.emit(
            "progress",
            5,
            f"Running File Transformer for {skill.name}",
            {"agent": "File Transformer", "agent_state": "running", "target_skill": skill.name, "files": filenames},
        )
        await context.emit(
            "progress",
            5,
            f"File Transformer Completed for {skill.name}",
            {"agent": "File Transformer", "agent_state": "done", "target_skill": skill.name, "files": filenames},
        )
    return SkillExecutionContext(
        message=context.message,
        history=context.history,
        attachments=prepared_attachments,
        data_profiles=prepared_profiles,
        llm=context.llm,
        emit=context.emit,
    )


async def _resolve_invocation(skill: SkillSpec, context: SkillExecutionContext) -> SkillInvocation:
    if skill.argument_resolver == "message":
        arguments = {"message": _message_with_recent_focus(context)}
    elif skill.argument_resolver == "differential_analysis_json":
        if context.emit is not None:
            await context.emit(
                "progress",
                5,
                f"Resolving Skill Arguments: {skill.name}",
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


def _message_with_recent_focus(context: SkillExecutionContext) -> str:
    return build_skill_message_with_context(context.message, context.history)


async def _run_gene_function_research_path(
    invocation: SkillInvocation,
    _context: SkillExecutionContext,
) -> dict[str, Any]:
    return await asyncio.to_thread(run_gene_function_research_path_query, invocation.arguments["message"])


async def _run_gene_info_query(
    invocation: SkillInvocation,
    _context: SkillExecutionContext,
) -> dict[str, Any]:
    return await asyncio.to_thread(run_gene_info_query, invocation.arguments["message"])


async def _run_gene_phenotype_prediction(
    invocation: SkillInvocation,
    _context: SkillExecutionContext,
) -> dict[str, Any]:
    return await asyncio.to_thread(run_gene_phenotype_prediction, invocation.arguments["message"])


async def _run_gene_mutant_query(
    invocation: SkillInvocation,
    _context: SkillExecutionContext,
) -> dict[str, Any]:
    return await asyncio.to_thread(run_gene_mutant_query, invocation.arguments["message"])


async def _run_primer_query(
    invocation: SkillInvocation,
    context: SkillExecutionContext,
) -> dict[str, Any]:
    message = invocation.arguments["message"]
    classification = await classify_primer_query(message, context.llm)
    return await asyncio.to_thread(run_primer_query, message, classification)


async def _run_blast_query(
    invocation: SkillInvocation,
    context: SkillExecutionContext,
) -> dict[str, Any]:
    message = invocation.arguments["message"]
    classification = await classify_blast_query(message, context.llm)
    return await asyncio.to_thread(run_blast_query, message, classification, context.attachments)


async def _run_trait2gene_query(
    invocation: SkillInvocation,
    context: SkillExecutionContext,
) -> dict[str, Any]:
    message = invocation.arguments["message"]
    classification = await classify_trait2gene_query(message, context.llm)
    return await asyncio.to_thread(run_trait2gene_query, message, classification)


async def _run_differential_protein(
    invocation: SkillInvocation,
    context: SkillExecutionContext,
) -> dict[str, Any]:
    return await run_omics_analysis_graph(
        data_family="proteomics",
        attachments=context.attachments,
        arguments=invocation.arguments,
        data_profiles=context.data_profiles,
        runner=run_differential_protein_analysis,
        emit=context.emit,
    )


async def _run_differential_transcriptomics(
    invocation: SkillInvocation,
    context: SkillExecutionContext,
) -> dict[str, Any]:
    return await run_omics_analysis_graph(
        data_family="transcriptomics",
        attachments=context.attachments,
        arguments=invocation.arguments,
        data_profiles=context.data_profiles,
        runner=run_differential_transcriptomics_analysis,
        emit=context.emit,
    )


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
    "query_gene_info": SkillExecutorBinding(
        name="query_gene_info",
        mode="deterministic_query",
        run=_run_gene_info_query,
    ),
    "gene_phenotype_prediction": SkillExecutorBinding(
        name="gene_phenotype_prediction",
        mode="deterministic_query",
        run=_run_gene_phenotype_prediction,
    ),
    "gene_mutant_query": SkillExecutorBinding(
        name="gene_mutant_query",
        mode="deterministic_query",
        run=_run_gene_mutant_query,
    ),
    "primer_query": SkillExecutorBinding(
        name="primer_query",
        mode="deterministic_query",
        run=_run_primer_query,
    ),
    "blast_query": SkillExecutorBinding(
        name="blast_query",
        mode="deterministic_query",
        run=_run_blast_query,
    ),
    "trait2gene_query": SkillExecutorBinding(
        name="trait2gene_query",
        mode="deterministic_query",
        run=_run_trait2gene_query,
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

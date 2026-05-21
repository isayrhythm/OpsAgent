from __future__ import annotations

import ast
import asyncio
import contextlib
import csv
import io
import json
import textwrap
from typing import Any, Awaitable, Callable

from backend.app.config import DATA_DIR, EXECUTION_TIMEOUT_SECONDS, PROJECT_ROOT
from backend.app.llm.prompts import CODE_GENERATOR_SYSTEM_PROMPT
from backend.app.schemas import UploadedFileSummary
from backend.app.services.deepseek_client import DeepSeekClient
from backend.app.services.differential_protein import run_differential_protein_analysis
from backend.app.services.differential_transcriptomics import run_differential_transcriptomics_analysis
from backend.app.services.result_evaluator import compact_value
from backend.app.services.skill_loader import SkillSpec


Emit = Callable[[str, int, str, Any | None], Awaitable[None]]
MAX_RETRY_FEEDBACK_CHARS = 8000

FORBIDDEN_IMPORTS = {"os", "subprocess", "shutil", "socket", "pathlib"}
FORBIDDEN_NAMES = {"eval", "exec", "compile", "open", "__import__"}


class SkillCodeExecutionError(RuntimeError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def _bounded_retry_feedback(feedback: dict[str, Any]) -> dict[str, Any]:
    bounded = compact_value(feedback)
    text = json.dumps(bounded, ensure_ascii=False)
    if len(text) <= MAX_RETRY_FEEDBACK_CHARS:
        return bounded
    return {
        "previous_code": textwrap.shorten(str(feedback.get("previous_code") or ""), width=2500, placeholder="..."),
        "previous_error": feedback.get("previous_error"),
        "evaluation": compact_value(feedback.get("evaluation")),
        "previous_result_summary": "<omitted because retry feedback was too large>",
        "instruction": "上一轮结果过大或无效。请只返回回答用户问题所需的最小 JSON 结构，不要返回整段大文本或大量列表。",
    }


def _validate_code(code: str) -> None:
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name.split(".")[0] for alias in node.names]
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module.split(".")[0])
            blocked = FORBIDDEN_IMPORTS.intersection(names)
            if blocked:
                raise ValueError(f"Generated code imports forbidden modules: {', '.join(sorted(blocked))}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_NAMES:
            raise ValueError(f"Generated code calls forbidden function: {node.func.id}")
        if isinstance(node, ast.Attribute) and node.attr in {"remove", "unlink", "rmdir", "rmtree"}:
            raise ValueError(f"Generated code uses forbidden attribute: {node.attr}")


def _query_gene_expression_locally(message: str) -> dict[str, Any]:
    csv_path = DATA_DIR / "example_gene_expression.csv"
    rows: list[dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    gene_ids = sorted({row["gene_id"] for row in rows if row["gene_id"] in message})
    tissues = sorted({row["tissue"] for row in rows if row["tissue"] in message.lower()})

    filtered = [
        {
            "gene_id": row["gene_id"],
            "tissue": row["tissue"],
            "expr": float(row["expr"]),
        }
        for row in rows
        if (not gene_ids or row["gene_id"] in gene_ids)
        and (not tissues or row["tissue"] in tissues)
    ]
    return {"records": filtered, "count": len(filtered)}


async def generate_skill_code(
    message: str,
    skill: SkillSpec,
    llm: DeepSeekClient,
    emit: Emit | None = None,
    retry_feedback: dict[str, Any] | None = None,
    is_retry: bool = False,
) -> str:
    if not llm.available:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    feedback_text = ""
    if retry_feedback is not None:
        bounded_feedback = _bounded_retry_feedback(retry_feedback)
        feedback_text = (
            "\n\n上一次尝试没有解决问题，请根据以下反馈修正代码。"
            "不要重复同样的错误。\n"
            f"{json.dumps(bounded_feedback, ensure_ascii=False)}"
        )

    messages = [
        {
            "role": "system",
            "content": CODE_GENERATOR_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": (
                f"项目根目录: {PROJECT_ROOT}\n"
                f"数据目录: {DATA_DIR}\n"
                f"用户请求: {message}\n\n"
                f"Skill 定义:\n{skill.content}"
                f"{feedback_text}"
            ),
        },
    ]
    response = ""
    status_text = f"正在重新调用 {skill.name} 智能体" if is_retry else f"正在调用 {skill.name} 智能体"
    async for delta in llm.stream_chat(
        messages,
        model=llm.settings.code_model,
        temperature=0,
        max_tokens=2000,
    ):
        response += delta
        if emit is not None:
            await emit("thinking_delta", 5, status_text, {"delta": delta, "delta_length": len(delta)})
    return _strip_code_fence(response)


async def run_generated_skill_code(
    message: str,
    skill: SkillSpec,
    llm: DeepSeekClient,
    emit: Emit | None = None,
    retry_feedback: dict[str, Any] | None = None,
    is_retry: bool = False,
) -> tuple[str, Any]:
    code = await generate_skill_code(message, skill, llm, emit, retry_feedback, is_retry)
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_execute_code_sync, code),
            timeout=EXECUTION_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise SkillCodeExecutionError(str(exc), code) from exc
    return code, result


def _execute_code_sync(code: str) -> Any:
    _validate_code(code)
    real_import = __import__

    def safe_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
        root_name = name.split(".")[0]
        if root_name in FORBIDDEN_IMPORTS:
            raise ImportError(f"Import of {root_name} is forbidden")
        return real_import(name, globals, locals, fromlist, level)

    namespace: dict[str, Any] = {
        "__builtins__": {
            "__import__": safe_import,
            "len": len,
            "min": min,
            "max": max,
            "sum": sum,
            "sorted": sorted,
            "float": float,
            "int": int,
            "str": str,
            "list": list,
            "dict": dict,
            "set": set,
            "tuple": tuple,
            "range": range,
            "enumerate": enumerate,
            "zip": zip,
            "any": any,
            "all": all,
            "isinstance": isinstance,
            "round": round,
            "abs": abs,
            "print": print,
            "Exception": Exception,
            "ValueError": ValueError,
        },
        "PROJECT_ROOT": str(PROJECT_ROOT),
        "DATA_DIR": str(DATA_DIR),
    }
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        exec(compile(code, "<skill_code>", "exec"), namespace)

    if "result" in namespace:
        result = namespace["result"]
    else:
        output = stdout.getvalue().strip()
        result = json.loads(output) if output else None
    json.dumps(result, ensure_ascii=False)
    return result


async def execute_skill(
    message: str,
    skill: SkillSpec,
    llm: DeepSeekClient,
    emit: Emit | None = None,
    *,
    attachments: list[UploadedFileSummary] | None = None,
    data_profiles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if skill.name == "differential_protein_analysis":
        profile_families = {
            str(profile.get("data_family"))
            for profile in (data_profiles or [])
            if profile.get("status") in {"profiled", "ready"}
        }
        if profile_families and "proteomics" not in profile_families:
            return {
                "mode": "deterministic_analysis",
                "result": {
                    "error": "上传文件没有被识别为蛋白组表达矩阵，不能调用蛋白差异分析。",
                    "data_profiles": data_profiles or [],
                },
            }
        result = await asyncio.to_thread(run_differential_protein_analysis, message, attachments or [])
        return {
            "mode": "deterministic_analysis",
            "result": result,
        }

    if skill.name == "differential_transcriptomics_analysis":
        profile_families = {
            str(profile.get("data_family"))
            for profile in (data_profiles or [])
            if profile.get("status") in {"profiled", "ready"}
        }
        if profile_families and "transcriptomics" not in profile_families:
            return {
                "mode": "deterministic_analysis",
                "result": {
                    "error": "上传文件没有被识别为转录组 counts 表达矩阵，不能调用转录组差异分析。",
                    "data_profiles": data_profiles or [],
                },
            }
        result = await asyncio.to_thread(run_differential_transcriptomics_analysis, message, attachments or [])
        return {
            "mode": "deterministic_analysis",
            "result": result,
        }

    if not llm.available and skill.name == "query_gene_expression":
        return {
            "mode": "local_fallback",
            "result": _query_gene_expression_locally(message),
        }

    code, result = await run_generated_skill_code(message, skill, llm, emit)
    return {
        "mode": "generated_code",
        "code": textwrap.shorten(code.replace("\n", " "), width=500, placeholder="..."),
        "result": result,
    }


async def retry_skill(
    message: str,
    skill: SkillSpec,
    llm: DeepSeekClient,
    *,
    previous_code: str | None = None,
    previous_result: Any = None,
    previous_error: str | None = None,
    evaluation: dict[str, Any] | None = None,
    emit: Emit | None = None,
) -> dict[str, Any]:
    retry_feedback = {
        "previous_code": previous_code,
        "previous_result_summary": compact_value(previous_result),
        "previous_error": previous_error,
        "evaluation": evaluation,
    }
    code, result = await run_generated_skill_code(message, skill, llm, emit, retry_feedback, is_retry=True)
    return {
        "mode": "generated_code_retry",
        "code": textwrap.shorten(code.replace("\n", " "), width=500, placeholder="..."),
        "result": result,
        "retry_feedback": compact_value(retry_feedback),
    }

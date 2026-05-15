from __future__ import annotations

import ast
import asyncio
import contextlib
import csv
import io
import json
import textwrap
from typing import Any

from backend.app.config import DATA_DIR, EXECUTION_TIMEOUT_SECONDS, PROJECT_ROOT
from backend.app.llm.prompts import CODE_GENERATOR_SYSTEM_PROMPT
from backend.app.services.deepseek_client import DeepSeekClient
from backend.app.services.skill_loader import SkillSpec


FORBIDDEN_IMPORTS = {"os", "subprocess", "shutil", "socket", "pathlib"}
FORBIDDEN_NAMES = {"eval", "exec", "compile", "open", "__import__"}


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


async def generate_skill_code(message: str, skill: SkillSpec, llm: DeepSeekClient) -> str:
    if not llm.available:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")

    response = await llm.chat(
        [
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
                ),
            },
        ],
        model=llm.settings.code_model,
        temperature=0,
        max_tokens=2000,
    )
    return _strip_code_fence(response)


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


async def execute_skill(message: str, skill: SkillSpec, llm: DeepSeekClient) -> dict[str, Any]:
    if not llm.available and skill.name == "query_gene_expression":
        return {
            "mode": "local_fallback",
            "result": _query_gene_expression_locally(message),
        }

    code = await generate_skill_code(message, skill, llm)
    result = await asyncio.wait_for(
        asyncio.to_thread(_execute_code_sync, code),
        timeout=EXECUTION_TIMEOUT_SECONDS,
    )
    return {
        "mode": "generated_code",
        "code": textwrap.shorten(code.replace("\n", " "), width=500, placeholder="..."),
        "result": result,
    }

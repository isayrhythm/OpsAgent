from __future__ import annotations

import json
from typing import Any

from backend.app.llm.calls import chat_json
from backend.app.llm.prompts import RESULT_EVALUATOR_SYSTEM_PROMPT
from backend.app.services.deepseek_client import DeepSeekClient
from backend.app.services.skill_loader import SkillSpec


MAX_STRING_LENGTH = 10000
MAX_LIST_ITEMS = 30
MAX_DICT_ITEMS = 24
MAX_EVALUATOR_INPUT_CHARS = 20000


def compact_value(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        if len(value) <= MAX_STRING_LENGTH:
            return value
        return value[:MAX_STRING_LENGTH] + f"... <truncated {len(value) - MAX_STRING_LENGTH} chars>"
    if isinstance(value, list):
        compacted = [compact_value(item, depth + 1) for item in value[:MAX_LIST_ITEMS]]
        if len(value) > MAX_LIST_ITEMS:
            compacted.append(f"<truncated {len(value) - MAX_LIST_ITEMS} items>")
        return compacted
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_DICT_ITEMS:
                compacted["<truncated>"] = f"{len(value) - MAX_DICT_ITEMS} keys"
                break
            compacted[str(key)] = compact_value(item, depth + 1)
        return compacted
    return value


def heuristic_evaluation(result: Any, error: str | None = None) -> dict[str, Any]:
    if error:
        return {
            "category": "retry_code",
            "answered": False,
            "reason": f"执行失败: {error}",
            "missing": ["valid_code_execution"],
        }
    if result is None:
        return {
            "category": "retry_code",
            "answered": False,
            "reason": "执行结果为空",
            "missing": ["non_empty_result"],
        }
    if isinstance(result, list) and not result:
        return {
            "category": "not_found",
            "answered": False,
            "reason": "查询结果为空列表",
            "missing": ["matching_records"],
        }
    if isinstance(result, dict):
        matches = result.get("matches")
        if isinstance(matches, list) and not matches:
            return {
                "category": "not_found",
                "answered": False,
                "reason": "matches 为空",
                "missing": ["matching_records"],
            }
        if result.get("error"):
            return {
                "category": "need_user_input",
                "answered": False,
                "reason": str(result.get("error")),
                "missing": ["user_input"],
            }
    return {
        "category": "answer",
        "answered": True,
        "reason": "结果非空",
        "missing": [],
    }


async def evaluate_skill_result(
    *,
    user_message: str,
    resolved_message: str,
    skill: SkillSpec,
    result: Any,
    llm: DeepSeekClient,
    error: str | None = None,
) -> dict[str, Any]:
    fallback = heuristic_evaluation(result, error)
    if not llm.available:
        return fallback

    payload = {
        "user_message": user_message,
        "resolved_message": resolved_message,
        "skill": {
            "name": skill.name,
            "description": skill.description,
            "data_paths": skill.data_paths,
        },
        "execution_error": error,
        "result_summary": compact_value(result),
        "output_schema": {
            "category": "answer | partial | not_found | need_user_input | retry_code",
            "answered": "boolean",
            "reason": "string",
            "missing": ["string"],
            "retry_instruction": "string, only when category is retry_code",
        },
    }
    payload_text = json.dumps(payload, ensure_ascii=False)
    if len(payload_text) > MAX_EVALUATOR_INPUT_CHARS:
        return {
            "category": "retry_code",
            "answered": False,
            "reason": f"评估输入过大（{len(payload_text)} chars），执行结果可能返回了过多无关内容",
            "missing": ["bounded_result"],
            "retry_instruction": (
                "重新生成代码时只返回回答用户问题所需的最小结果。"
                "不要返回整段大文本、完整文件内容或大量列表；"
                "优先返回命中的物种、标准ID、匹配来源和与问题直接相关的字段。"
            ),
        }
    try:
        evaluated = await chat_json(
            llm,
            [
                {"role": "system", "content": RESULT_EVALUATOR_SYSTEM_PROMPT},
                {"role": "user", "content": payload_text},
            ],
            model=llm.settings.router_model,
            temperature=0,
            max_tokens=500,
        )
    except Exception:
        return fallback

    category = evaluated.get("category")
    if category not in {"answer", "partial", "not_found", "need_user_input", "retry_code"}:
        return fallback
    return {
        "category": category,
        "answered": bool(evaluated.get("answered")),
        "reason": str(evaluated.get("reason", "")),
        "missing": evaluated.get("missing") if isinstance(evaluated.get("missing"), list) else [],
        "retry_instruction": str(evaluated.get("retry_instruction", "")),
    }

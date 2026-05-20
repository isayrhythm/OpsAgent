from __future__ import annotations

import json
import re
from dataclasses import dataclass

from backend.app.schemas import ChatHistoryMessage
from backend.app.services.deepseek_client import DeepSeekClient
from backend.app.llm.prompts import ROUTER_SYSTEM_PROMPT
from backend.app.services.skill_loader import SkillSpec


GENE_ID_PATTERN = re.compile(
    r"(loc_os\d+g\d+|agis_os\d+g\d+|zm\d+[a-z]*\d+|glyma\.\d+g\d+|gmw82\.\d+g\d+)",
    re.I,
)
ARABIDOPSIS_ID_PATTERN = re.compile(r"\bAT\dG\d+\b", re.I)
GENE_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9_.-]{2,})(?![A-Za-z0-9_.-])")
GENE_TOKEN_STOPWORDS = {
    "annotation",
    "arabidopsis",
    "cold",
    "corn",
    "evidence",
    "expression",
    "function",
    "gene",
    "genes",
    "glycine",
    "info",
    "leaf",
    "maize",
    "oryza",
    "query",
    "rice",
    "root",
    "soy",
    "soybean",
    "version",
    "zea",
    "t2t",
    "auto",
    "autogptq",
    "awq",
    "bf16",
    "bit",
    "chatglm",
    "cpu",
    "fp16",
    "fp32",
    "gguf",
    "gpu",
    "gptq",
    "int4",
    "int8",
    "llama",
    "llm",
    "llm.int8",
}
GENE_INFO_CONTEXT_TERMS = (
    "基因",
    "信息",
    "注释",
    "功能",
    "位置",
    "长度",
    "证据",
    "查",
    "命中",
    "对应",
    "相关",
    "什么",
    "水稻",
    "玉米",
    "大豆",
    "gene",
    "annotation",
    "function",
    "evidence",
    "rice",
    "maize",
    "soy",
    "soybean",
)
GENE_INFO_WEAK_CONTEXT_TERMS = {"什么", "相关", "信息"}
EXPRESSION_CONTEXT_TERMS = ("拟南芥", "表达量", "gene expression")
EXPLICIT_QUERY_CONSTRAINT_TERMS = (
    "表达量",
    "表达证据",
    "表达",
    "t2t",
    "版本",
    "标准",
    "位置",
    "长度",
    "注释",
    "功能",
    "结构域",
    "转录本",
    "文献",
    "性状",
    "go",
    "kegg",
    "expression",
    "annotation",
    "function",
    "domain",
    "transcript",
    "version",
)
SPECIES_HINTS = (
    ("水稻", ("水稻", "rice", "oryza", "loc_os", "agis_os")),
    ("玉米", ("玉米", "maize", "corn", "zea", "zm")),
    ("大豆", ("大豆", "soy", "soybean", "glycine", "glyma", "gmw82")),
)


FOLLOW_UP_MARKERS = (
    "这个",
    "该",
    "它",
    "上一个",
    "刚才",
    "上述",
    "前面",
    "其中",
    "什么",
    "哪些",
    "为什么",
    "怎么",
    "表达证据",
    "功能呢",
    "呢",
)


@dataclass(frozen=True)
class RouteDecision:
    skill: SkillSpec | None
    skills: list[SkillSpec]
    resolved_message: str


def _json_from_text(text: str) -> dict[str, object]:
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        text = match.group(0)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("router response must be a JSON object")
    return value


def _fallback_resolve_message(message: str, history: list[ChatHistoryMessage]) -> str:
    message = message.strip()
    is_follow_up = bool(history) and (
        len(message) <= 40 or any(marker in message for marker in FOLLOW_UP_MARKERS)
    )
    if not is_follow_up:
        return message

    recent = history[-6:]
    context = "\n".join(f"{item.role}: {item.content}" for item in recent)
    return (
        "以下是最近对话上下文，仅用于补全当前问题中省略的基因 ID、物种或查询对象；"
        "必须回答当前用户问题，不要继承历史中的其他并列任务或旧限定条件。\n"
        f"{context}\n"
        f"当前用户问题: {message}"
    )


def _dedupe_skills(skills: list[SkillSpec]) -> list[SkillSpec]:
    seen: set[str] = set()
    result: list[SkillSpec] = []
    for skill in skills:
        if skill.name in seen:
            continue
        seen.add(skill.name)
        result.append(skill)
    return result


def _extract_gene_terms(message: str) -> list[str]:
    terms: list[str] = []
    for match in GENE_TOKEN_PATTERN.finditer(message):
        term = match.group(1).strip(".,;:!?，。；：！？、")
        if len(term) < 3:
            continue
        lowered = term.lower()
        if lowered in GENE_TOKEN_STOPWORDS:
            continue
        terms.append(term)
    return terms


def _mentions_expression_query(message: str) -> bool:
    normalized = message.lower()
    return (
        any(term in message for term in EXPRESSION_CONTEXT_TERMS if not term.isascii())
        or ARABIDOPSIS_ID_PATTERN.search(message) is not None
        or "gene expression" in normalized
    )


def _mentions_gene_info_query(message: str) -> bool:
    normalized = message.lower()
    if ARABIDOPSIS_ID_PATTERN.search(message):
        return False
    if GENE_ID_PATTERN.search(message):
        stripped = message.strip()
        return bool(GENE_ID_PATTERN.fullmatch(stripped)) or any(token in normalized for token in GENE_INFO_CONTEXT_TERMS)

    terms = _extract_gene_terms(message)
    if not terms:
        return False
    has_context = any(
        token in normalized
        for token in GENE_INFO_CONTEXT_TERMS
        if token not in GENE_INFO_WEAK_CONTEXT_TERMS
    )
    return has_context


def _current_question_has_specific_gene(message: str) -> bool:
    return GENE_ID_PATTERN.search(message) is not None or bool(_extract_gene_terms(message))


def _has_explicit_query_constraint(message: str) -> bool:
    normalized = message.lower()
    return any(term in normalized for term in EXPLICIT_QUERY_CONSTRAINT_TERMS)


def _is_open_ended_follow_up(message: str) -> bool:
    normalized = message.lower()
    return (
        len(message.strip()) <= 60
        and any(marker in message for marker in FOLLOW_UP_MARKERS)
        and not _has_explicit_query_constraint(normalized)
    )


def _species_hint_from_text(*values: str) -> str:
    joined = "\n".join(values)
    normalized = joined.lower()
    for label, markers in SPECIES_HINTS:
        if any(marker in normalized for marker in markers):
            return label
    return ""


def _sanitize_resolved_message(current_message: str, resolved_message: str) -> str:
    current_terms = _extract_gene_terms(current_message)
    if not current_terms or not _is_open_ended_follow_up(current_message):
        return resolved_message
    if not _mentions_gene_info_query(current_message) and not _mentions_gene_info_query(resolved_message):
        return resolved_message

    species_hint = _species_hint_from_text(current_message, resolved_message)
    species_prefix = f"{species_hint} " if species_hint else ""
    term_text = "、".join(current_terms)
    return f"查询{species_prefix}{term_text} 的基因信息和本次查到的命中结果；只回答当前问题：{current_message}"


def _filter_stale_skill_selection(selected: list[SkillSpec], current_message: str) -> list[SkillSpec]:
    if _mentions_expression_query(current_message):
        selected = [skill for skill in selected if skill.name != "query_gene_info"]
    if _current_question_has_specific_gene(current_message) and not _mentions_expression_query(current_message):
        selected = [skill for skill in selected if skill.name != "query_gene_expression"]
    return _dedupe_skills(selected)


def _filter_invalid_builtin_skill_selection(selected: list[SkillSpec], resolved_message: str) -> list[SkillSpec]:
    filtered: list[SkillSpec] = []
    for skill in selected:
        if skill.name == "query_gene_info" and not _mentions_gene_info_query(resolved_message):
            continue
        if skill.name == "query_gene_expression" and not _mentions_expression_query(resolved_message):
            continue
        filtered.append(skill)
    return _dedupe_skills(filtered)


def _fallback_skills(message: str, skills: list[SkillSpec]) -> list[SkillSpec]:
    selected: list[SkillSpec] = []
    normalized = message.lower()

    if _mentions_gene_info_query(message):
        gene_info_skill = next((skill for skill in skills if skill.name == "query_gene_info"), None)
        if gene_info_skill is not None:
            selected.append(gene_info_skill)

    for skill in skills:
        if skill.name.lower() in normalized:
            selected.append(skill)
        if skill.name == "query_gene_expression" and _mentions_expression_query(message):
            selected.append(skill)
    return _dedupe_skills(selected)


async def route_skill(
    message: str,
    skills: list[SkillSpec],
    llm: DeepSeekClient,
    history: list[ChatHistoryMessage] | None = None,
) -> RouteDecision:
    history = history or []
    if not skills:
        return RouteDecision(skill=None, skills=[], resolved_message=message)

    if not llm.available:
        resolved_message = _fallback_resolve_message(message, history)
        resolved_message = _sanitize_resolved_message(message, resolved_message)
        selected = _filter_invalid_builtin_skill_selection(_fallback_skills(resolved_message, skills), resolved_message)
        selected = _filter_stale_skill_selection(selected, message)
        return RouteDecision(skill=selected[0] if selected else None, skills=selected, resolved_message=resolved_message)

    catalog = [
        {
            "name": skill.name,
            "description": skill.description,
            "trigger": skill.trigger,
            "execution_mode": skill.execution_mode,
            "data_paths": skill.data_paths,
        }
        for skill in skills
    ]
    response = await llm.chat(
        [
            {
                "role": "system",
                "content": ROUTER_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "current_message": message,
                        "history": [
                            {"role": item.role, "content": item.content}
                            for item in history[-8:]
                        ],
                        "skills": catalog,
                        "output_schema": {
                            "depends_on_history": "boolean",
                            "resolved_message": "string",
                            "skill_names": ["string"],
                            "reason": "string",
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        model=llm.settings.router_model,
        temperature=0,
        max_tokens=500,
    )
    try:
        routed = _json_from_text(response)
    except Exception:
        resolved_message = _fallback_resolve_message(message, history)
        resolved_message = _sanitize_resolved_message(message, resolved_message)
        selected = _filter_invalid_builtin_skill_selection(_fallback_skills(resolved_message, skills), resolved_message)
        selected = _filter_stale_skill_selection(selected, message)
        return RouteDecision(skill=selected[0] if selected else None, skills=selected, resolved_message=resolved_message)

    resolved_message = routed.get("resolved_message")
    if not isinstance(resolved_message, str) or not resolved_message.strip():
        resolved_message = message
    resolved_message = resolved_message.strip()
    resolved_message = _sanitize_resolved_message(message, resolved_message)
    skill_names = routed.get("skill_names")
    if not isinstance(skill_names, list):
        skill_name = routed.get("skill_name")
        skill_names = [skill_name] if isinstance(skill_name, str) else []
    selected = [
        skill
        for name in skill_names
        if isinstance(name, str)
        for skill in skills
        if skill.name == name
    ]
    deterministic = _fallback_skills(resolved_message, skills)
    selected = _filter_invalid_builtin_skill_selection([*deterministic, *selected], resolved_message)
    selected = _filter_stale_skill_selection(selected, message)
    return RouteDecision(skill=selected[0] if selected else None, skills=selected, resolved_message=resolved_message)

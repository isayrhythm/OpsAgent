from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from backend.app.schemas import ChatHistoryMessage


WEB_SEARCH_MODES = {"off", "auto", "force"}


@dataclass
class SearchQuery:
    query: str
    purpose: str = ""
    priority: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "purpose": self.purpose,
            "priority": self.priority,
        }


@dataclass
class SearchPlan:
    mode: str
    need_search: bool
    reason: str
    search_intent: str = ""
    queries: list[SearchQuery] = field(default_factory=list)
    freshness_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "need_search": self.need_search,
            "reason": self.reason,
            "search_intent": self.search_intent,
            "queries": [query.to_dict() for query in self.queries],
            "freshness_required": self.freshness_required,
        }


def normalize_web_search_mode(value: str | None, *, legacy_web_search: bool = False) -> str:
    mode = str(value or "").strip().lower()
    if mode in WEB_SEARCH_MODES:
        return mode
    return "force" if legacy_web_search else "auto"


async def plan_web_search(
    message: str,
    *,
    history: list[ChatHistoryMessage] | None = None,
    mode: str = "auto",
    providers: list[str] | None = None,
    llm: Any | None = None,
    max_queries: int = 5,
) -> SearchPlan:
    normalized_mode = normalize_web_search_mode(mode)
    if normalized_mode == "off":
        return SearchPlan(mode="off", need_search=False, reason="Web search mode is off.")

    fallback = _heuristic_plan(message, mode=normalized_mode, max_queries=max_queries)
    if llm is None or not getattr(llm, "available", False):
        return fallback

    try:
        payload = {
            "today": date.today().isoformat(),
            "mode": normalized_mode,
            "providers": providers or [],
            "user_message": message,
            "history": [
                {"role": item.role, "content": item.content}
                for item in (history or [])[-8:]
            ],
            "max_queries": max_queries,
        }
        content = await llm.chat(
            [
                {"role": "system", "content": _PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            model=getattr(getattr(llm, "settings", None), "router_model", None),
            temperature=0,
            max_tokens=900,
            response_format={"type": "json_object"},
        )
        return _plan_from_json(content, fallback=fallback, mode=normalized_mode, max_queries=max_queries)
    except Exception:
        return fallback


def _heuristic_plan(message: str, *, mode: str, max_queries: int) -> SearchPlan:
    text = " ".join(message.split())
    need_search = mode == "force" or _looks_time_sensitive(text)
    queries = [SearchQuery(query=text, purpose="Original user question", priority=1)] if need_search and text else []
    return SearchPlan(
        mode=mode,
        need_search=need_search,
        reason="Forced search." if mode == "force" else "Heuristic search decision.",
        search_intent=text if need_search else "",
        queries=queries[:max_queries],
        freshness_required=_looks_time_sensitive(text),
    )


def _looks_time_sensitive(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "today",
        "latest",
        "recent",
        "current",
        "now",
        "news",
        "weather",
        "price",
        "schedule",
        "version",
        "release",
        "2025",
        "2026",
        "今天",
        "今日",
        "现在",
        "最新",
        "近期",
        "新闻",
        "天气",
        "价格",
        "股价",
        "汇率",
        "政策",
        "法规",
        "版本",
        "发布",
    )
    return any(marker in lowered for marker in markers)


def _plan_from_json(content: str, *, fallback: SearchPlan, mode: str, max_queries: int) -> SearchPlan:
    data = _json_object(content)
    if not isinstance(data, dict):
        return fallback

    need_search = bool(data.get("need_search"))
    if mode == "force":
        need_search = True

    queries: list[SearchQuery] = []
    raw_queries = data.get("queries")
    if isinstance(raw_queries, list):
        for index, item in enumerate(raw_queries[:max_queries], start=1):
            if isinstance(item, str):
                query = item.strip()
                purpose = ""
                priority = index
            elif isinstance(item, dict):
                query = str(item.get("query") or "").strip()
                purpose = str(item.get("purpose") or "").strip()
                priority = _int_or_default(item.get("priority"), index)
            else:
                continue
            if query and query not in [existing.query for existing in queries]:
                queries.append(SearchQuery(query=query, purpose=purpose, priority=priority))

    if need_search and not queries:
        queries = fallback.queries

    queries = sorted(queries, key=lambda item: item.priority)[:max_queries]
    return SearchPlan(
        mode=mode,
        need_search=need_search,
        reason=str(data.get("reason") or fallback.reason),
        search_intent=str(data.get("search_intent") or fallback.search_intent),
        queries=queries,
        freshness_required=bool(data.get("freshness_required", fallback.freshness_required)),
    )


def _json_object(content: str) -> Any:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return None


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


_PLANNER_SYSTEM_PROMPT = """
You are OpsAgent's built-in Search Query Rewriter.
Return JSON only.

Decide whether web search is needed, then rewrite the user's request into 1-5 focused search queries.

Rules:
- If mode is "force", need_search must be true.
- If mode is "auto", use search only for fresh/current facts, news, weather, prices, schedules,
  laws/policies, recent publications, product versions, or facts likely to have changed.
- Do not search for ordinary chat, writing, summarization, stable concepts, or questions answerable
  from uploaded files or prior tool results.
- Resolve vague follow-ups with the recent history.
- Make queries concise and search-engine friendly.
- If Tavily is among providers and the user wrote Chinese, include at least one English keyword query.
- If Quark is among providers and the user wrote Chinese, include at least one Chinese query.

Schema:
{
  "need_search": true,
  "reason": "short reason",
  "search_intent": "what evidence is needed",
  "queries": [
    {"query": "query text", "purpose": "why this query exists", "priority": 1}
  ],
  "freshness_required": true
}
"""

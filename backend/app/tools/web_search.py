from __future__ import annotations

import asyncio
from typing import Any

import httpx

from backend.app.config import (
    QUARK_SEARCH_API_KEY,
    QUARK_SEARCH_BASE_URL,
    QUARK_SEARCH_CONTENT_TYPE,
    QUARK_SEARCH_QUERY_REWRITE,
    QUARK_SEARCH_SERVICE_ID,
    QUARK_SEARCH_WORKSPACE,
    TAVILY_API_KEY,
    TAVILY_BASE_URL,
    WEB_SEARCH_PROVIDER,
)
from backend.app.schemas import ChatHistoryMessage
from backend.app.tools.tool_runner import ToolRetryPolicy, ToolRunnerError, run_tool


class WebSearchError(RuntimeError):
    pass


WEB_SEARCH_ANSWER_REQUIREMENTS = [
    "If web_search.context and web_search.sources are present, treat them as evidence from this turn's web search.",
    "When summarizing web search evidence, cite the relevant sentence with existing source indexes from web_search.sources, using [1], [2], or [1][3].",
    "Do not cite indexes that are not present in web_search.sources.",
    "Do not invent source titles, URLs, authors, dates, or findings that are not present in web_search.context.",
]


# 搜索是外部 I/O，timeout、网络抖动、429、5xx 可以重试。
# 认证、配置、请求格式错误要快速失败，不做无意义重试。
SEARCH_RETRY_POLICY = ToolRetryPolicy(
    max_attempts=3,
    initial_delay_seconds=0.4,
    backoff_multiplier=2.0,
    retry_exceptions=(httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError),
    retry_if_exception=lambda exc: _is_retryable_http_error(exc),
)


async def search_web(
    query: str,
    *,
    max_results: int = 6,
    search_depth: str = "advanced",
    history: list[ChatHistoryMessage] | None = None,
    providers: list[str] | None = None,
) -> dict[str, Any]:
    selected_providers = _normalize_providers(providers)
    if len(selected_providers) > 1:
        return await _search_multiple_providers(
            query,
            providers=selected_providers,
            max_results=max_results,
            search_depth=search_depth,
            history=history,
        )
    provider = selected_providers[0]
    if provider in {"quark", "aliyun", "opensearch"}:
        return await _search_quark(query, max_results=max_results, history=history)
    if provider != "tavily":
        raise WebSearchError(f"Unsupported WEB_SEARCH_PROVIDER: {provider}")
    return await _search_tavily(query, max_results=max_results, search_depth=search_depth)


async def search_web_queries(
    queries: list[dict[str, Any] | str],
    *,
    max_results: int = 10,
    per_query_results: int = 5,
    search_depth: str = "advanced",
    history: list[ChatHistoryMessage] | None = None,
    providers: list[str] | None = None,
) -> dict[str, Any]:
    normalized_queries = _normalize_queries(queries)
    if not normalized_queries:
        return {"provider": "multi_query", "query": "", "queries": [], "results": []}

    selected_providers = _normalize_providers(providers)
    tasks = [
        search_web(
            item["query"],
            max_results=per_query_results,
            search_depth=search_depth,
            history=history,
            providers=selected_providers,
        )
        for item in normalized_queries
    ]
    responses = await asyncio.gather(*tasks, return_exceptions=True)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for item, response in zip(normalized_queries, responses):
        if isinstance(response, Exception):
            errors.append({"query": item["query"], "error": str(response)})
            continue
        for result in response.get("results") or []:
            if not isinstance(result, dict):
                continue
            results.append(
                {
                    **result,
                    "query": item["query"],
                    "query_purpose": item.get("purpose") or "",
                    "query_priority": item.get("priority") or 1,
                }
            )

    deduped_results = _dedupe_results(results)
    if len(deduped_results) < min(3, max_results) and len(normalized_queries) < 5:
        followup = _followup_query(normalized_queries)
        if followup:
            followup_item = {
                "query": followup,
                "purpose": "Follow-up search because the first pass returned too little evidence.",
                "priority": max(item["priority"] for item in normalized_queries) + 1,
            }
            try:
                response = await search_web(
                    followup,
                    max_results=per_query_results,
                    search_depth=search_depth,
                    history=history,
                    providers=selected_providers,
                )
                normalized_queries.append(followup_item)
                for result in response.get("results") or []:
                    if not isinstance(result, dict):
                        continue
                    results.append(
                        {
                            **result,
                            "query": followup_item["query"],
                            "query_purpose": followup_item["purpose"],
                            "query_priority": followup_item["priority"],
                        }
                    )
            except Exception as exc:
                errors.append({"query": followup, "error": str(exc)})

    if not results and errors:
        raise WebSearchError("; ".join(f"{item['query']}: {item['error']}" for item in errors))

    ranked_results = _rank_results(_dedupe_results(results), [item["query"] for item in normalized_queries])
    ranked_results = _ensure_provider_diversity(ranked_results, selected_providers, max_results)
    return {
        "provider": "multi_query",
        "providers": selected_providers,
        "query": " | ".join(item["query"] for item in normalized_queries),
        "queries": normalized_queries,
        "results": ranked_results[:max_results],
        "errors": errors,
    }


def _normalize_providers(providers: list[str] | None) -> list[str]:
    raw = providers if providers else [WEB_SEARCH_PROVIDER or "tavily"]
    normalized: list[str] = []
    for item in raw:
        provider = str(item or "").strip().lower()
        if provider in {"aliyun", "opensearch"}:
            provider = "quark"
        if provider and provider not in normalized:
            normalized.append(provider)
    return normalized or ["tavily"]


def _normalize_queries(queries: list[dict[str, Any] | str]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(queries, start=1):
        if isinstance(item, str):
            query = item.strip()
            purpose = ""
            priority = index
        elif isinstance(item, dict):
            query = str(item.get("query") or "").strip()
            purpose = str(item.get("purpose") or "").strip()
            try:
                priority = int(item.get("priority") or index)
            except (TypeError, ValueError):
                priority = index
        else:
            continue
        key = " ".join(query.lower().split())
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append({"query": query, "purpose": purpose, "priority": priority})
    return sorted(normalized, key=lambda item: item["priority"])


def _followup_query(queries: list[dict[str, Any]]) -> str:
    if not queries:
        return ""
    base = queries[0]["query"]
    lowered = base.lower()
    if "latest" in lowered or "today" in lowered or "2026" in lowered:
        candidate = f"{base} sources"
    else:
        candidate = f"{base} latest reliable sources"
    existing = {" ".join(item["query"].lower().split()) for item in queries}
    key = " ".join(candidate.lower().split())
    return "" if key in existing else candidate


async def _search_multiple_providers(
    query: str,
    *,
    providers: list[str],
    max_results: int,
    search_depth: str,
    history: list[ChatHistoryMessage] | None,
) -> dict[str, Any]:
    per_provider_limit = max(1, max_results)
    tasks = [
        search_web(
            query,
            max_results=per_provider_limit,
            search_depth=search_depth,
            history=history,
            providers=[provider],
        )
        for provider in providers
    ]
    responses = await asyncio.gather(*tasks, return_exceptions=True)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    provider_queries: dict[str, str] = {}
    for provider, response in zip(providers, responses):
        if isinstance(response, Exception):
            errors.append({"provider": provider, "error": str(response)})
            continue
        provider_queries[provider] = str(response.get("query") or query)
        for item in response.get("results") or []:
            if not isinstance(item, dict):
                continue
            results.append({**item, "provider": provider})
    if not results and errors:
        raise WebSearchError("; ".join(f"{item['provider']}: {item['error']}" for item in errors))
    return {
        "provider": "multi",
        "providers": providers,
        "provider_queries": provider_queries,
        "query": query,
        "results": _dedupe_results(results),
        "errors": errors,
    }


def _dedupe_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in results:
        url = str(item.get("url") or "").strip()
        key = _canonical_url(url)
        if key and key in seen_urls:
            continue
        if key:
            seen_urls.add(key)
        deduped.append(item)
    return deduped


def _canonical_url(url: str) -> str:
    return url.strip().rstrip("/")


def _rank_results(results: list[dict[str, Any]], queries: list[str]) -> list[dict[str, Any]]:
    query_terms = {
        term
        for query in queries
        for term in query.lower().replace("/", " ").replace("-", " ").split()
        if len(term) >= 3
    }

    scored: list[tuple[float, int, dict[str, Any]]] = []
    for index, item in enumerate(results):
        text = f"{item.get('title') or ''} {item.get('content') or ''}".lower()
        term_hits = sum(1 for term in query_terms if term in text)
        try:
            api_score = float(item.get("score") or 0)
        except (TypeError, ValueError):
            api_score = 0.0
        try:
            priority = int(item.get("query_priority") or 1)
        except (TypeError, ValueError):
            priority = 1
        evidence_score = api_score + term_hits * 0.05 + max(0, 6 - priority) * 0.01
        scored_item = {**item, "evidence_score": round(evidence_score, 4)}
        scored.append((evidence_score, -index, scored_item))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item for _score, _index, item in scored]


def _ensure_provider_diversity(
    results: list[dict[str, Any]],
    providers: list[str],
    max_results: int,
) -> list[dict[str, Any]]:
    if len(providers) <= 1 or max_results <= 1:
        return results
    window = results[:max_results]
    present = {str(item.get("provider") or "") for item in window}
    next_window = list(window)
    for provider in providers:
        if provider in present:
            continue
        candidate = next((item for item in results[max_results:] if item.get("provider") == provider), None)
        if candidate is None:
            continue
        replace_at = len(next_window) - 1
        provider_counts: dict[str, int] = {}
        for item in next_window:
            current_provider = str(item.get("provider") or "")
            provider_counts[current_provider] = provider_counts.get(current_provider, 0) + 1
        for index in range(len(next_window) - 1, -1, -1):
            current_provider = str(next_window[index].get("provider") or "")
            if current_provider and provider_counts.get(current_provider, 0) > 1:
                replace_at = index
                break
        next_window[replace_at] = candidate
        present = {str(item.get("provider") or "") for item in next_window}
    rest = [item for item in results if item not in next_window]
    return next_window + rest


async def _search_tavily(query: str, *, max_results: int = 6, search_depth: str = "advanced") -> dict[str, Any]:
    if not TAVILY_API_KEY:
        raise WebSearchError("TAVILY_API_KEY is not configured")

    normalized_query = " ".join(query.split())
    payload = {
        "query": normalized_query,
        "search_depth": search_depth,
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": False,
    }
    headers = {
        "Authorization": f"Bearer {TAVILY_API_KEY}",
        "Content-Type": "application/json",
    }

    async def request() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.post(f"{TAVILY_BASE_URL.rstrip('/')}/search", json=payload, headers=headers)
            if response.status_code == 400 and payload.get("search_depth") != "basic":
                payload["search_depth"] = "basic"
                response = await client.post(f"{TAVILY_BASE_URL.rstrip('/')}/search", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}

    try:
        data = await run_tool("Tavily Search", request, policy=SEARCH_RETRY_POLICY)
    except ToolRunnerError as exc:
        raise WebSearchError(f"Tavily search failed: {exc}") from exc

    results = []
    for item in data.get("results", []):
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "content": str(item.get("content") or ""),
                "score": item.get("score"),
            }
        )

    return {
        "provider": "tavily",
        "query": data.get("query") or normalized_query,
        "answer": data.get("answer"),
        "results": results,
    }


async def _search_quark(
    query: str,
    *,
    max_results: int = 6,
    history: list[ChatHistoryMessage] | None = None,
) -> dict[str, Any]:
    if not QUARK_SEARCH_API_KEY:
        raise WebSearchError("QUARK_SEARCH_API_KEY is not configured")

    normalized_query = " ".join(query.split())
    payload = {
        "history": _quark_history(history or []),
        "query": normalized_query,
        "query_rewrite": QUARK_SEARCH_QUERY_REWRITE,
        "top_k": max_results,
        "content_type": QUARK_SEARCH_CONTENT_TYPE,
    }
    headers = {
        "Authorization": f"Bearer {QUARK_SEARCH_API_KEY}",
        "Content-Type": "application/json",
    }
    url = (
        f"{QUARK_SEARCH_BASE_URL.rstrip('/')}/v3/openapi/workspaces/"
        f"{QUARK_SEARCH_WORKSPACE}/web-search/{QUARK_SEARCH_SERVICE_ID}"
    )

    async def request() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}

    try:
        data = await run_tool("Quark Search", request, policy=SEARCH_RETRY_POLICY)
    except ToolRunnerError as exc:
        raise WebSearchError(f"Quark search failed: {exc}") from exc

    return {
        "provider": "quark",
        "query": _first_string(data, ("query", "rewritten_query", "rewrite_query")) or normalized_query,
        "answer": _first_string(data, ("answer", "summary")),
        "results": _normalize_quark_results(data)[:max_results],
        "raw": data,
    }


def _quark_history(history: list[ChatHistoryMessage]) -> list[dict[str, str]]:
    items = [{"role": "system", "content": "你是一个机器人助手"}]
    for item in history[-8:]:
        role = "assistant" if item.role in {"assistant", "agent"} else "user"
        content = str(item.content or "").strip()
        if content:
            items.append({"role": role, "content": content})
    return items


def _normalize_quark_results(data: Any) -> list[dict[str, Any]]:
    candidates = _candidate_result_items(data)
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in candidates:
        title = _string_from_keys(item, ("title", "name", "site_name"))
        url = _string_from_keys(item, ("url", "link", "href", "source_url"))
        content = _string_from_keys(item, ("content", "snippet", "summary", "text", "description"))
        score = item.get("score") or item.get("rank_score") or item.get("rerank_score")
        if not url and not title and not content:
            continue
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        results.append(
            {
                "title": title or "Untitled",
                "url": url,
                "content": content,
                "score": score,
            }
        )
    return results


def _candidate_result_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        for key in ("results", "result", "items", "documents", "docs", "search_results", "web_pages"):
            nested = value.get(key)
            if isinstance(nested, list):
                dict_items = [item for item in nested if isinstance(item, dict)]
                if dict_items and any(_looks_like_search_item(item) for item in dict_items):
                    return dict_items
            if isinstance(nested, dict):
                found = _candidate_result_items(nested)
                if found:
                    return found
        for nested in value.values():
            found = _candidate_result_items(nested)
            if found:
                return found
    if isinstance(value, list):
        dict_items = [item for item in value if isinstance(item, dict)]
        if dict_items and any(_looks_like_search_item(item) for item in dict_items):
            return dict_items
        for item in value:
            found = _candidate_result_items(item)
            if found:
                return found
    return []


def _looks_like_search_item(item: dict[str, Any]) -> bool:
    keys = set(item)
    return bool(keys & {"url", "link", "href", "source_url", "title", "snippet", "content", "summary"})


def _first_string(value: Any, keys: tuple[str, ...]) -> str:
    if not isinstance(value, dict):
        return ""
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    for nested in value.values():
        found = _first_string(nested, keys)
        if found:
            return found
    return ""


def _string_from_keys(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            parts = [str(part).strip() for part in value if str(part).strip()]
            if parts:
                return " ".join(parts)
    return ""


def _is_retryable_http_error(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or 500 <= status_code < 600
    return False


def format_web_search_context(search_result: dict[str, Any]) -> str:
    results = _referenceable_search_results(search_result)
    if not results:
        return "联网搜索已启用，但没有返回可用结果。"

    lines = [
        "联网搜索已启用。以下是搜索结果摘要，请结合这些结果回答。",
        f"查询：{search_result.get('query') or ''}",
    ]
    queries = search_result.get("queries")
    if isinstance(queries, list) and queries:
        lines.append("Search queries:")
        for query_item in queries:
            if not isinstance(query_item, dict):
                continue
            purpose = str(query_item.get("purpose") or "").strip()
            suffix = f" - {purpose}" if purpose else ""
            lines.append(f"- {query_item.get('query') or ''}{suffix}")
    providers = search_result.get("providers")
    if isinstance(providers, list) and providers:
        lines.append(f"搜索源：{', '.join(str(item) for item in providers)}")
    for index, item in results:
        provider = str(item.get("provider") or search_result.get("provider") or "").strip()
        provider_label = f" ({provider})" if provider and provider != "multi" else ""
        query_label = f"Query: {item.get('query') or ''}" if item.get("query") else ""
        lines.extend(
            [
                f"[{index}] {item.get('title') or 'Untitled'}{provider_label}",
                f"URL: {item.get('url') or ''}",
                query_label,
                f"摘要: {item.get('content') or ''}",
            ]
        )
    return "\n".join(lines)


def web_search_sources(search_result: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for index, item in _referenceable_search_results(search_result):
        url = str(item.get("url") or "")
        provider = str(item.get("provider") or search_result.get("provider") or "").strip()
        source = {
            "index": index,
            "title": str(item.get("title") or f"Source {index}"),
            "url": url,
        }
        if provider:
            source["provider"] = provider
        sources.append(source)
    return sources


def _referenceable_search_results(search_result: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    results = search_result.get("results")
    if not isinstance(results, list):
        return []
    referenceable: list[tuple[int, dict[str, Any]]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        if not str(item.get("url") or "").strip():
            continue
        referenceable.append((len(referenceable) + 1, item))
    return referenceable

from __future__ import annotations

from typing import Any

import httpx

from backend.app.config import TAVILY_API_KEY, TAVILY_BASE_URL


class WebSearchError(RuntimeError):
    pass


async def search_web(query: str, *, max_results: int = 6, search_depth: str = "advanced") -> dict[str, Any]:
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

    try:
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.post(f"{TAVILY_BASE_URL.rstrip('/')}/search", json=payload, headers=headers)
            if response.status_code == 400 and search_depth != "basic":
                payload["search_depth"] = "basic"
                response = await client.post(f"{TAVILY_BASE_URL.rstrip('/')}/search", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
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
        "query": data.get("query") or normalized_query,
        "answer": data.get("answer"),
        "results": results,
    }


def format_web_search_context(search_result: dict[str, Any]) -> str:
    results = search_result.get("results")
    if not isinstance(results, list) or not results:
        return "联网搜索已启用，但没有返回可用结果。"

    lines = [
        "联网搜索已启用。以下是搜索结果摘要，请结合这些结果回答。",
        f"查询：{search_result.get('query') or ''}",
    ]
    for index, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue
        lines.extend(
            [
                f"[{index}] {item.get('title') or 'Untitled'}",
                f"URL: {item.get('url') or ''}",
                f"摘要: {item.get('content') or ''}",
            ]
        )
    return "\n".join(lines)


def web_search_sources(search_result: dict[str, Any]) -> list[dict[str, Any]]:
    results = search_result.get("results")
    if not isinstance(results, list):
        return []
    sources: list[dict[str, Any]] = []
    for index, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not url:
            continue
        sources.append(
            {
                "index": index,
                "title": str(item.get("title") or f"Source {index}"),
                "url": url,
            }
        )
    return sources

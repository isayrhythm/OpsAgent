import asyncio

from backend.app.schemas import ChatHistoryMessage
from backend.app.tools import web_search
from backend.app.tools.web_search import format_web_search_context, web_search_sources


def test_format_web_search_context_includes_sources() -> None:
    context = format_web_search_context(
        {
            "query": "opsagent",
            "results": [
                {
                    "title": "OpsAgent docs",
                    "url": "https://example.com/docs",
                    "content": "Search result summary.",
                }
            ],
        }
    )

    assert "opsagent" in context
    assert "OpsAgent docs" in context
    assert "https://example.com/docs" in context
    assert "Search result summary." in context


def test_format_web_search_context_handles_empty_results() -> None:
    assert "没有返回可用结果" in format_web_search_context({"query": "none", "results": []})


def test_web_search_sources_keeps_numbered_urls() -> None:
    sources = web_search_sources(
        {
            "results": [
                {"title": "First", "url": "https://example.com/1", "content": "one"},
                {"title": "Missing url", "content": "skip"},
                {"title": "Third", "url": "https://example.com/3", "content": "three"},
            ]
        }
    )

    assert sources == [
        {"index": 1, "title": "First", "url": "https://example.com/1"},
        {"index": 3, "title": "Third", "url": "https://example.com/3"},
    ]


def test_quark_search_normalizes_results_and_sends_history(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "data": {
                    "rewrite_query": "Hangzhou weather today",
                    "results": [
                        {
                            "title": "Hangzhou Weather",
                            "url": "https://example.com/weather",
                            "snippet": "Cloudy with light rain.",
                            "score": 0.8,
                        }
                    ],
                }
            }

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["payload"] = json
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(web_search, "WEB_SEARCH_PROVIDER", "quark")
    monkeypatch.setattr(web_search, "QUARK_SEARCH_API_KEY", "test-key")
    monkeypatch.setattr(web_search, "QUARK_SEARCH_BASE_URL", "http://search.example.com")
    monkeypatch.setattr(web_search, "QUARK_SEARCH_WORKSPACE", "default")
    monkeypatch.setattr(web_search, "QUARK_SEARCH_SERVICE_ID", "ops-web-search-001")
    monkeypatch.setattr(web_search.httpx, "AsyncClient", FakeClient)

    result = asyncio.run(
        web_search.search_web(
            "杭州今日天气怎么样",
            max_results=5,
            history=[
                ChatHistoryMessage(role="user", content="浙江的省会是哪里"),
                ChatHistoryMessage(role="assistant", content="杭州"),
            ],
        )
    )

    assert captured["url"] == "http://search.example.com/v3/openapi/workspaces/default/web-search/ops-web-search-001"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["payload"]["query"] == "杭州今日天气怎么样"
    assert captured["payload"]["query_rewrite"] is True
    assert captured["payload"]["top_k"] == 5
    assert captured["payload"]["content_type"] == "snippet"
    assert captured["payload"]["history"][-2:] == [
        {"role": "user", "content": "浙江的省会是哪里"},
        {"role": "assistant", "content": "杭州"},
    ]
    assert result["provider"] == "quark"
    assert result["query"] == "Hangzhou weather today"
    assert result["results"] == [
        {
            "title": "Hangzhou Weather",
            "url": "https://example.com/weather",
            "content": "Cloudy with light rain.",
            "score": 0.8,
        }
    ]


def test_multi_provider_search_merges_results(monkeypatch) -> None:
    async def fake_tavily(query, *, max_results, search_depth):
        return {
            "provider": "tavily",
            "query": query,
            "results": [
                {"title": "Tavily result", "url": "https://example.com/a", "content": "from tavily"}
            ],
        }

    async def fake_quark(query, *, max_results, history):
        return {
            "provider": "quark",
            "query": query,
            "results": [
                {"title": "Quark result", "url": "https://example.com/b", "content": "from quark"}
            ],
        }

    monkeypatch.setattr(web_search, "_search_tavily", fake_tavily)
    monkeypatch.setattr(web_search, "_search_quark", fake_quark)

    result = asyncio.run(web_search.search_web("query", providers=["tavily", "quark"]))

    assert result["provider"] == "multi"
    assert result["providers"] == ["tavily", "quark"]
    assert [item["provider"] for item in result["results"]] == ["tavily", "quark"]
    assert web_search.web_search_sources(result) == [
        {"index": 1, "title": "Tavily result", "url": "https://example.com/a", "provider": "tavily"},
        {"index": 2, "title": "Quark result", "url": "https://example.com/b", "provider": "quark"},
    ]


def test_search_web_queries_dedupes_and_tracks_query(monkeypatch) -> None:
    async def fake_search_web(query, **_kwargs):
        return {
            "provider": "tavily",
            "query": query,
            "results": [
                {"title": f"{query} primary", "url": "https://example.com/a", "content": "weather evidence"},
                {"title": f"{query} duplicate", "url": "https://example.com/a/", "content": "duplicate"},
                {"title": f"{query} secondary", "url": "https://example.com/b", "content": "other evidence"},
            ],
        }

    monkeypatch.setattr(web_search, "search_web", fake_search_web)

    result = asyncio.run(
        web_search.search_web_queries(
            [
                {"query": "Hangzhou weather today", "purpose": "weather", "priority": 1},
                {"query": "杭州 今日 天气", "purpose": "local", "priority": 2},
            ],
            providers=["tavily"],
            max_results=5,
        )
    )

    assert result["provider"] == "multi_query"
    assert result["queries"][0]["query"] == "Hangzhou weather today"
    assert len(result["results"]) == 2
    assert {item["query"] for item in result["results"]} <= {"Hangzhou weather today", "杭州 今日 天气"}

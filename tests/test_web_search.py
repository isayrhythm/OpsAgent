from backend.app.services.web_search import format_web_search_context, web_search_sources


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

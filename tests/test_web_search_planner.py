import asyncio
import json
from types import SimpleNamespace

from backend.app.tools.web_search_planner import normalize_web_search_mode, plan_web_search


def test_normalize_web_search_mode_keeps_legacy_force() -> None:
    assert normalize_web_search_mode(None, legacy_web_search=True) == "force"
    assert normalize_web_search_mode(None, legacy_web_search=False) == "auto"
    assert normalize_web_search_mode("off", legacy_web_search=True) == "off"


def test_off_mode_never_searches() -> None:
    plan = asyncio.run(plan_web_search("latest news", mode="off"))

    assert plan.need_search is False
    assert plan.queries == []


def test_auto_mode_uses_heuristic_when_llm_unavailable() -> None:
    plan = asyncio.run(plan_web_search("杭州今日天气怎么样", mode="auto"))

    assert plan.need_search is True
    assert plan.freshness_required is True
    assert plan.queries[0].query == "杭州今日天气怎么样"


def test_force_mode_uses_llm_rewritten_queries() -> None:
    class FakeLLM:
        available = True
        settings = SimpleNamespace(router_model="router")

        async def chat(self, messages, **kwargs):
            assert kwargs["response_format"] == {"type": "json_object"}
            payload = json.loads(messages[1]["content"])
            assert payload["mode"] == "force"
            return json.dumps(
                {
                    "need_search": False,
                    "reason": "forced by user",
                    "search_intent": "weather",
                    "queries": [
                        {"query": "Hangzhou weather today", "purpose": "english", "priority": 2},
                        {"query": "杭州 今日 天气", "purpose": "chinese", "priority": 1},
                    ],
                    "freshness_required": True,
                }
            )

    plan = asyncio.run(
        plan_web_search(
            "杭州今日天气怎么样",
            mode="force",
            providers=["tavily", "quark"],
            llm=FakeLLM(),
        )
    )

    assert plan.need_search is True
    assert [query.query for query in plan.queries] == ["杭州 今日 天气", "Hangzhou weather today"]

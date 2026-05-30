import asyncio
import json

import httpx

from backend.app.llm import deepseek
from backend.app.llm.deepseek import DeepSeekClient
from backend.app.llm.settings import LLMSettings


def make_client() -> DeepSeekClient:
    return DeepSeekClient(
        LLMSettings(
            api_key="test-key",
            base_url="https://deepseek.test",
            router_model="router",
            answer_model="answer",
            code_model="code",
        )
    )


def patch_transport(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        deepseek.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=transport, **kwargs),
    )


def test_chat_accumulates_usage(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 3,
                    "total_tokens": 14,
                    "prompt_cache_hit_tokens": 5,
                    "prompt_cache_miss_tokens": 6,
                },
            },
        )

    patch_transport(monkeypatch, handler)
    client = make_client()

    assert asyncio.run(client.chat([{"role": "user", "content": "hello"}])) == "ok"
    assert client.usage_snapshot() == {
        "prompt_tokens": 11,
        "completion_tokens": 3,
        "total_tokens": 14,
        "prompt_cache_hit_tokens": 5,
        "prompt_cache_miss_tokens": 6,
    }


def test_stream_chat_accumulates_usage_only_chunk(monkeypatch) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        body = "\n\n".join(
            [
                'data: {"choices":[{"delta":{"content":"generated "}}]}',
                'data: {"choices":[{"delta":{"content":"code"}}]}',
                (
                    'data: {"choices":[],"usage":{"prompt_tokens":21,'
                    '"completion_tokens":4,"total_tokens":25}}'
                ),
                "data: [DONE]",
            ]
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    patch_transport(monkeypatch, handler)
    client = make_client()

    async def collect() -> str:
        return "".join([delta async for delta in client.stream_chat([{"role": "user", "content": "write code"}])])

    assert asyncio.run(collect()) == "generated code"
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert client.usage_snapshot()["total_tokens"] == 25

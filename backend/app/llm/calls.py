from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


STREAMING_ENABLED = True
DeltaCallback = Callable[[str], Awaitable[None]]


async def chat_text(
    llm: Any,
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2000,
) -> str:
    return await llm.chat(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )


async def chat_json(
    llm: Any,
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0,
    max_tokens: int = 1000,
) -> dict[str, Any]:
    response = await llm.chat(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return parse_json_object(response)


async def stream_text(
    llm: Any,
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2000,
) -> AsyncIterator[str]:
    if not STREAMING_ENABLED:
        yield await chat_text(
            llm,
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return
    async for delta in llm.stream_chat(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    ):
        yield delta


async def complete_text(
    llm: Any,
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    emit_delta: DeltaCallback | None = None,
) -> str:
    answer = ""
    async for delta in stream_text(
        llm,
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    ):
        answer += delta
        if emit_delta is not None:
            await emit_delta(delta)
    return answer


def parse_json_object(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text.strip(), re.S)
    value = json.loads(match.group(0) if match else text)
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value

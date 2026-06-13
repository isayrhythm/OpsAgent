from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from backend.app.config import LLM_STREAM_TIMEOUT_SECONDS
from backend.app.llm.settings import LLMSettings, get_llm_settings


class DeepSeekClient:
    def __init__(self, settings: LLMSettings | None = None) -> None:
        self.settings = settings or get_llm_settings()
        self._usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 0,
        }

    @property
    def available(self) -> bool:
        return bool(self.settings.api_key)

    def usage_snapshot(self) -> dict[str, int]:
        return dict(self._usage)

    def _record_usage(self, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        for key in self._usage:
            try:
                self._usage[key] += int(usage.get(key) or 0)
            except (TypeError, ValueError):
                continue

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2000,
        response_format: dict[str, str] | None = None,
    ) -> str:
        if not self.available:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")

        payload: dict[str, Any] = {
            "model": model or self.settings.answer_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
        }
        if response_format is not None:
            payload["response_format"] = response_format
        headers = {"Authorization": f"Bearer {self.settings.api_key}"}

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.settings.base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
        self._record_usage(body.get("usage"))
        return body["choices"][0]["message"]["content"]

    async def stream_chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2000,
    ) -> AsyncIterator[str]:
        if not self.available:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")

        payload: dict[str, Any] = {
            "model": model or self.settings.answer_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "thinking": {"type": "disabled"},
        }
        headers = {"Authorization": f"Bearer {self.settings.api_key}"}

        async with httpx.AsyncClient(timeout=LLM_STREAM_TIMEOUT_SECONDS) as client:
            async with client.stream(
                "POST",
                f"{self.settings.base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if not data or data == "[DONE]":
                        continue
                    chunk = json.loads(data)
                    self._record_usage(chunk.get("usage"))
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {}).get("content")
                    if delta:
                        yield delta

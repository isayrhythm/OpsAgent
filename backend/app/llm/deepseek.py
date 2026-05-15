from __future__ import annotations

from typing import Any

import httpx

from backend.app.llm.settings import LLMSettings, get_llm_settings


class DeepSeekClient:
    def __init__(self, settings: LLMSettings | None = None) -> None:
        self.settings = settings or get_llm_settings()

    @property
    def available(self) -> bool:
        return bool(self.settings.api_key)

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2000,
    ) -> str:
        if not self.available:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")

        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {self.settings.api_key}"}

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.settings.base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
        return body["choices"][0]["message"]["content"]

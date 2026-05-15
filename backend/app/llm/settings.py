from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class LLMSettings:
    api_key: str
    base_url: str
    router_model: str
    answer_model: str
    code_model: str


def get_llm_settings() -> LLMSettings:
    return LLMSettings(
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        router_model=os.getenv("DEEPSEEK_ROUTER_MODEL", "deepseek-v4-flash"),
        answer_model=os.getenv("DEEPSEEK_ANSWER_MODEL", "deepseek-v4-pro"),
        code_model=os.getenv("DEEPSEEK_CODE_MODEL", "deepseek-v4-pro"),
    )

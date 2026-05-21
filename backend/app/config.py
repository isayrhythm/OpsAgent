from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

SKILL_DIR = Path(os.getenv("OPSAGENT_SKILL_DIR", PROJECT_ROOT / "skill"))
DATA_DIR = Path(os.getenv("OPSAGENT_DATA_DIR", PROJECT_ROOT / "data"))
MEMORY_DIR = Path(os.getenv("OPSAGENT_MEMORY_DIR", PROJECT_ROOT / "memory"))

EXECUTION_TIMEOUT_SECONDS = int(os.getenv("OPSAGENT_EXECUTION_TIMEOUT_SECONDS", "20"))
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
TAVILY_BASE_URL = os.getenv("TAVILY_BASE_URL", "https://api.tavily.com")

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

SKILL_DIR = Path(os.getenv("OPSAGENT_SKILL_DIR", PROJECT_ROOT / "skill"))
DATA_DIR = Path(os.getenv("OPSAGENT_DATA_DIR", PROJECT_ROOT / "data"))
MEMORY_DIR = Path(os.getenv("OPSAGENT_MEMORY_DIR", PROJECT_ROOT / "memory"))
CORS_ORIGINS = [
    item.strip()
    for item in os.getenv(
        "OPSAGENT_CORS_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173",
    ).split(",")
    if item.strip()
]

EXECUTION_TIMEOUT_SECONDS = int(os.getenv("OPSAGENT_EXECUTION_TIMEOUT_SECONDS", "20"))
LLM_STREAM_TIMEOUT_SECONDS = int(os.getenv("OPSAGENT_LLM_STREAM_TIMEOUT_SECONDS", "20"))
TASK_RETENTION_SECONDS = int(os.getenv("OPSAGENT_TASK_RETENTION_SECONDS", "120"))
UPLOAD_INTAKE_RETENTION_SECONDS = int(os.getenv("OPSAGENT_UPLOAD_INTAKE_RETENTION_SECONDS", "120"))
WEB_SEARCH_PROVIDER = os.getenv("WEB_SEARCH_PROVIDER", "tavily").strip().lower()
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
TAVILY_BASE_URL = os.getenv("TAVILY_BASE_URL", "https://api.tavily.com")
QUARK_SEARCH_API_KEY = os.getenv("QUARK_SEARCH_API_KEY", "")
QUARK_SEARCH_BASE_URL = os.getenv("QUARK_SEARCH_BASE_URL", "")
QUARK_SEARCH_WORKSPACE = os.getenv("QUARK_SEARCH_WORKSPACE", "default")
QUARK_SEARCH_SERVICE_ID = os.getenv("QUARK_SEARCH_SERVICE_ID", "ops-web-search-001")
QUARK_SEARCH_QUERY_REWRITE = os.getenv("QUARK_SEARCH_QUERY_REWRITE", "true").strip().lower() not in {
    "0",
    "false",
    "no",
}
QUARK_SEARCH_CONTENT_TYPE = os.getenv("QUARK_SEARCH_CONTENT_TYPE", "snippet")

COMMAND_TOOL_ENABLED = os.getenv("OPSAGENT_COMMAND_TOOL_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
}
COMMAND_TOOL_BACKEND = os.getenv("OPSAGENT_COMMAND_TOOL_BACKEND", "auto").strip().lower()
COMMAND_TOOL_TIMEOUT_SECONDS = int(os.getenv("OPSAGENT_COMMAND_TOOL_TIMEOUT_SECONDS", "20"))
COMMAND_TOOL_MAX_OUTPUT_CHARS = int(os.getenv("OPSAGENT_COMMAND_TOOL_MAX_OUTPUT_CHARS", "12000"))
COMMAND_TOOL_WORKDIR = Path(os.getenv("OPSAGENT_COMMAND_TOOL_WORKDIR", MEMORY_DIR / "command_tool"))
COMMAND_TOOL_CWD = Path(os.getenv("OPSAGENT_COMMAND_TOOL_CWD", PROJECT_ROOT))
COMMAND_TOOL_DOCKER_IMAGE = os.getenv("OPSAGENT_COMMAND_TOOL_DOCKER_IMAGE", "ubuntu:22.04")

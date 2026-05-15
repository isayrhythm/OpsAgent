from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

SKILL_DIR = Path(os.getenv("OPSAGENT_SKILL_DIR", PROJECT_ROOT / "skill"))
DATA_DIR = Path(os.getenv("OPSAGENT_DATA_DIR", PROJECT_ROOT / "data"))

EXECUTION_TIMEOUT_SECONDS = int(os.getenv("OPSAGENT_EXECUTION_TIMEOUT_SECONDS", "20"))

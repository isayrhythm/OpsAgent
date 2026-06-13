import asyncio

import pytest

from backend.app.tools.command_tool import CommandToolError, execute_shell_command, plan_shell_command


class OfflineLLM:
    available = False


def test_command_tool_blocks_sensitive_env_access() -> None:
    with pytest.raises(CommandToolError, match="sensitive"):
        asyncio.run(execute_shell_command("cat .env", backend="native"))


def test_command_tool_blocks_destructive_command() -> None:
    with pytest.raises(CommandToolError, match="blocked"):
        asyncio.run(execute_shell_command("rm -rf /tmp/example", backend="native"))


def test_command_planner_does_not_fallback_without_llm() -> None:
    with pytest.raises(RuntimeError, match="command planner model is unavailable"):
        asyncio.run(plan_shell_command({"question": "看看当前目录"}, "", [], OfflineLLM()))

import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.app.tools.command_tool import CommandToolError, command_tool_spec, execute_shell_command, plan_shell_command


class OfflineLLM:
    available = False


class PlannerLLM:
    available = True
    settings = SimpleNamespace(router_model="router")

    def __init__(self) -> None:
        self.messages = []

    async def chat(self, messages, **_kwargs):
        self.messages.append(messages)
        return '{"command":"pwd","reason":"inspect current project directory"}'


def test_command_tool_blocks_sensitive_env_access() -> None:
    with pytest.raises(CommandToolError, match="sensitive"):
        asyncio.run(execute_shell_command("cat .env", backend="native"))


def test_command_tool_spec_exposes_io_schema() -> None:
    spec = command_tool_spec()

    assert spec.execution_mode == "builtin_tool"
    assert spec.input_schema is not None
    assert spec.output_schema is not None
    assert spec.input_schema["required"] == ["command"]
    assert "stdout" in spec.output_schema["properties"]
    assert "stderr" in spec.output_schema["properties"]


def test_command_tool_blocks_destructive_command() -> None:
    with pytest.raises(CommandToolError, match="blocked"):
        asyncio.run(execute_shell_command("rm -rf /tmp/example", backend="native"))


def test_command_planner_does_not_fallback_without_llm() -> None:
    with pytest.raises(RuntimeError, match="command planner model is unavailable"):
        asyncio.run(plan_shell_command({"question": "看看当前目录"}, "", [], OfflineLLM()))


def test_command_tool_runs_from_project_root() -> None:
    result = asyncio.run(execute_shell_command("pwd", backend="native"))

    assert result["status"] == "completed"
    assert result["workdir"].endswith("OpsAgent")
    assert "OpsAgent" in result["stdout"]


def test_command_planner_receives_runtime_context() -> None:
    llm = PlannerLLM()

    plan = asyncio.run(plan_shell_command({"question": "看看当前目录"}, "", [], llm))

    payload = json.loads(llm.messages[0][1]["content"])
    runtime_context = payload["runtime_context"]
    assert plan.command == "pwd"
    assert runtime_context["command_cwd"]
    assert runtime_context["shell_dialect"]
    assert runtime_context["command_cwd_host_path"].endswith("OpsAgent")

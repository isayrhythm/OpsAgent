from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.config import (
    COMMAND_TOOL_BACKEND,
    COMMAND_TOOL_DOCKER_IMAGE,
    COMMAND_TOOL_ENABLED,
    COMMAND_TOOL_MAX_OUTPUT_CHARS,
    COMMAND_TOOL_TIMEOUT_SECONDS,
    COMMAND_TOOL_WORKDIR,
)
from backend.app.llm.calls import chat_json
from backend.app.llm.prompts import COMMAND_TOOL_PLANNER_SYSTEM_PROMPT
from backend.app.schemas import ChatHistoryMessage
from backend.app.services.deepseek_client import DeepSeekClient
from backend.app.services.skill_loader import SkillSpec
from backend.app.tools.tool_runner import ToolRetryPolicy, run_tool


COMMAND_TOOL_NAME = "Shell Command"
COMMAND_TOOL_SPEC = SkillSpec(
    name=COMMAND_TOOL_NAME,
    description="Run one safe local shell command in an isolated working directory.",
    version="1",
    trigger=(
        "Use only when the user explicitly asks to inspect local files, list directories, "
        "run a local CLI check, count/convert local files, or perform shell-based local processing."
    ),
    execution_mode="builtin_tool",
    data_paths=[],
    path=Path("__builtin__/shell_command"),
    content="",
    answer_requirements=[
        "Return stdout, stderr, exit_code, and the executed command.",
        "Do not access secrets, environment variables, network, sudo, SSH, package managers, or destructive commands.",
    ],
)


class CommandToolError(ValueError):
    pass


@dataclass(frozen=True)
class CommandPlan:
    command: str
    reason: str = ""


def command_tool_spec() -> SkillSpec:
    return COMMAND_TOOL_SPEC


async def plan_shell_command(
    task: dict[str, Any],
    context: str,
    history: list[ChatHistoryMessage],
    llm: DeepSeekClient,
) -> CommandPlan:
    if not llm.available:
        raise RuntimeError("DeepSeek command planner model is unavailable; cannot plan shell command")
    payload = {
        "task": task,
        "dependency_context": context[:5000],
        "history": [{"role": item.role, "content": item.content} for item in history[-4:]],
        "rules": [
            "Return exactly one shell command.",
            "Prefer read-only inspection, conversion, counting, listing, or local CLI checks.",
            "Do not access secrets, environment variables, network, SSH, sudo, package managers, or destructive commands.",
            "Do not use absolute paths outside the command workdir unless explicitly provided as an allowed input.",
        ],
        "output_schema": {"command": "string", "reason": "short reason"},
    }
    data = await chat_json(
        llm,
        [
            {"role": "system", "content": COMMAND_TOOL_PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        model=llm.settings.router_model,
        temperature=0,
        max_tokens=500,
    )
    command = str(data.get("command") or "").strip()
    return CommandPlan(command=command, reason=str(data.get("reason") or "").strip())


async def execute_shell_command(
    command: str,
    *,
    task_id: str = "",
    backend: str | None = None,
    timeout_seconds: int | None = None,
    max_output_chars: int | None = None,
) -> dict[str, Any]:
    # 命令工具也走统一 runner，但默认只执行一次。
    # 命令失败通常需要 evaluator/planner 修复命令，而不是盲目重放同一条。
    return await run_tool(
        COMMAND_TOOL_NAME,
        lambda: _execute_shell_command_once(
            command,
            task_id=task_id,
            backend=backend,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        ),
        policy=ToolRetryPolicy(max_attempts=1, fatal_exceptions=(CommandToolError,), wrap_exceptions=False),
    )


async def _execute_shell_command_once(
    command: str,
    *,
    task_id: str = "",
    backend: str | None = None,
    timeout_seconds: int | None = None,
    max_output_chars: int | None = None,
) -> dict[str, Any]:
    if not COMMAND_TOOL_ENABLED:
        raise CommandToolError("Shell command tool is disabled")
    _validate_shell_command(command)
    resolved_backend = _resolve_backend(backend or COMMAND_TOOL_BACKEND)
    workdir = _prepare_workdir(task_id)
    timeout = timeout_seconds or COMMAND_TOOL_TIMEOUT_SECONDS
    max_chars = max_output_chars or COMMAND_TOOL_MAX_OUTPUT_CHARS
    args, cwd = _runner_args(command, resolved_backend, workdir)
    env = _safe_env(workdir)
    timed_out = False
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            timed_out = True
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
    except FileNotFoundError as exc:
        raise CommandToolError(f"Command backend is unavailable: {resolved_backend}") from exc

    stdout = _decode(stdout_bytes)
    stderr = _decode(stderr_bytes)
    return {
        "status": "completed" if not timed_out and process.returncode == 0 else "failed",
        "analysis": "shell_command",
        "backend": resolved_backend,
        "command": command,
        "workdir": str(workdir),
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "stdout": _truncate(stdout, max_chars),
        "stderr": _truncate(stderr, max_chars),
    }


def _validate_shell_command(command: str) -> None:
    text = str(command or "").strip()
    if not text:
        raise CommandToolError("Shell command is empty")
    if len(text) > 2000:
        raise CommandToolError("Shell command is too long")
    lowered = text.lower()
    forbidden_literals = (
        ".env",
        "id_rsa",
        "known_hosts",
        "authorized_keys",
        "/proc/self/environ",
        "credential",
        "secret",
        "api_key",
        "apikey",
    )
    if any(item in lowered for item in forbidden_literals):
        raise CommandToolError("Shell command attempts to access sensitive data")
    forbidden_patterns = (
        r"(^|[\s;&|])(sudo|su|ssh|scp|sftp|telnet|ftp|nc|netcat)([\s;&|]|$)",
        r"(^|[\s;&|])(curl|wget)([\s;&|]|$)",
        r"(^|[\s;&|])(apt|apt-get|yum|dnf|pacman|pip\s+install|npm\s+install)([\s;&|]|$)",
        r"(^|[\s;&|])(rm\s+-[^\n;|&]*[rf]|rmdir|del|erase|format|mkfs|shutdown|reboot|poweroff)([\s;&|]|$)",
        r"(^|[\s;&|])(dd\s+if=|killall|pkill|chmod\s+-r|chown\s+-r)([\s;&|]|$)",
        r":\s*\(\)\s*\{",
    )
    for pattern in forbidden_patterns:
        if re.search(pattern, lowered):
            raise CommandToolError("Shell command contains a blocked operation")


def _resolve_backend(backend: str) -> str:
    requested = (backend or "auto").strip().lower()
    if requested not in {"auto", "wsl", "docker", "native"}:
        raise CommandToolError(f"Unsupported command backend: {backend}")
    if requested != "auto":
        return requested
    if platform.system().lower().startswith("win") and shutil.which("wsl.exe"):
        return "wsl"
    if shutil.which("bash"):
        return "native"
    if shutil.which("docker"):
        return "docker"
    return "native"


def _prepare_workdir(task_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", task_id or "task")[:48]
    run_id = f"{safe_id}_{uuid.uuid4().hex[:8]}"
    workdir = COMMAND_TOOL_WORKDIR / run_id
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir.resolve()


def _runner_args(command: str, backend: str, workdir: Path) -> tuple[list[str], Path | None]:
    if backend == "wsl":
        workdir_text = _bash_quote(str(workdir))
        wrapped = f"cd \"$(wslpath -a {workdir_text})\" && {command}"
        return ["wsl.exe", "bash", "-lc", wrapped], None
    if backend == "docker":
        mount = f"{workdir}:/workspace"
        return [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            mount,
            "-w",
            "/workspace",
            COMMAND_TOOL_DOCKER_IMAGE,
            "bash",
            "-lc",
            command,
        ], None
    if shutil.which("bash"):
        return ["bash", "-lc", command], workdir
    if platform.system().lower().startswith("win"):
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command], workdir
    return ["sh", "-lc", command], workdir


def _safe_env(workdir: Path) -> dict[str, str]:
    env: dict[str, str] = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(workdir),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
    }
    if platform.system().lower().startswith("win"):
        for key in ("SystemRoot", "WINDIR", "ComSpec", "PATHEXT"):
            if os.environ.get(key):
                env[key] = os.environ[key]
    return env


def _decode(value: bytes) -> str:
    if not value:
        return ""
    if value.count(b"\x00") > max(2, len(value) // 8):
        try:
            return value.decode("utf-16le", errors="replace")
        except UnicodeError:
            pass
    return value.decode("utf-8", errors="replace").replace("\x00", "")


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"


def _bash_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"

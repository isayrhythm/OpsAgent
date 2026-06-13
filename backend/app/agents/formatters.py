from __future__ import annotations

import json
from typing import Any

from backend.app.agents.state import AgentState


def llm_history_messages(state: AgentState) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in state.get("history", [])[-20:]:
        role = "assistant" if item.role in {"assistant", "agent"} else "user"
        messages.append({"role": role, "content": item.content})
    messages.append({"role": "user", "content": state["message"]})
    return messages


def attachment_context(state: AgentState) -> list[dict[str, Any]]:
    return [
        {
            "file_id": item.file_id,
            "filename": item.filename,
            "content_type": item.content_type,
            "size": item.size,
            "path": item.path,
        }
        for item in state.get("attachments", [])
    ]


def detached_files_prompt(state: AgentState) -> str:
    detached = state.get("detached_files", [])
    if not detached:
        return ""
    filenames = "、".join(item.filename for item in detached[-8:])
    return f"当前对话已卸载文件：{filenames}。这些文件当前不可用；若历史消息说它们还在，以当前附件状态为准。"


def command_completed(output: Any) -> bool:
    return isinstance(output, dict) and output.get("status") == "completed" and output.get("exit_code") == 0


def command_repair_context(base_context: str, command_outputs: list[dict[str, Any]]) -> str:
    last = command_outputs[-1] if command_outputs else {}
    output = last.get("output") if isinstance(last, dict) else {}
    if not isinstance(output, dict):
        output = {}
    failure = {
        "attempt": last.get("attempt"),
        "command": output.get("command") or (last.get("plan") or {}).get("command"),
        "exit_code": output.get("exit_code"),
        "timed_out": output.get("timed_out"),
        "stdout": str(output.get("stdout") or "")[:3000],
        "stderr": str(output.get("stderr") or "")[:3000],
    }
    return "\n".join(
        [
            base_context,
            "",
            "Previous shell command failed. Generate one repaired command.",
            "Previous failure:",
            json.dumps(failure, ensure_ascii=False),
        ]
    )


def format_command_answer(command_outputs: list[dict[str, Any]]) -> str:
    if not command_outputs:
        return "没有执行命令。"
    lines = ["命令执行结果："]
    for item in command_outputs:
        output = item.get("output") if isinstance(item, dict) else {}
        if not isinstance(output, dict):
            output = {}
        command = output.get("command") or (item.get("plan") or {}).get("command")
        lines.append("")
        lines.append(f"- 第 {item.get('attempt', 1)} 次：`{command}`")
        lines.append(f"  exit_code: `{output.get('exit_code')}`")
        stdout = str(output.get("stdout") or "").strip()
        stderr = str(output.get("stderr") or "").strip()
        if stdout:
            lines.append("  stdout:")
            lines.append("```text")
            lines.append(stdout)
            lines.append("```")
        if stderr:
            lines.append("  stderr:")
            lines.append("```text")
            lines.append(stderr)
            lines.append("```")
    return "\n".join(lines)

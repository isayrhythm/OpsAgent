from __future__ import annotations

from typing import Any

from backend.app.agents.formatters import command_completed, command_repair_context
from backend.app.agents.state import AgentState, Emit
from backend.app.llm.deepseek import DeepSeekClient


def make_command_node(llm: DeepSeekClient, emit: Emit, deps: Any):
    async def command_node(state: AgentState) -> AgentState:
        task = {
            "id": "command",
            "title": "执行本地命令",
            "question": state["message"],
            "tools": [deps.COMMAND_TOOL_NAME],
        }
        base_context = state["message"]
        context = base_context
        outputs: list[dict[str, Any]] = []
        for attempt in range(1, 3):
            plan = await deps.plan_shell_command(task, context, state.get("history", []), llm)
            await emit(
                "progress",
                5,
                f"{'Running' if attempt == 1 else 'Repairing'} Shell Command: {plan.command}",
                {
                    "agent": deps.COMMAND_TOOL_NAME,
                    "agent_state": "running",
                    "attempt": attempt,
                    "command": plan.command,
                    "reason": plan.reason,
                },
            )
            output = await deps.execute_shell_command(plan.command, task_id="agent_command")
            outputs.append(
                {
                    "tool_name": deps.COMMAND_TOOL_NAME,
                    "attempt": attempt,
                    "plan": {"command": plan.command, "reason": plan.reason},
                    "output": output,
                }
            )
            if command_completed(output):
                break
            if attempt == 1:
                context = command_repair_context(base_context, outputs)
        await emit(
            "progress",
            5,
            "Shell Command Completed",
            {"agent": deps.COMMAND_TOOL_NAME, "agent_state": "done", "attempts": len(outputs)},
        )
        return {"command_outputs": outputs}

    return command_node

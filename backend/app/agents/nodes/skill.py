from __future__ import annotations

import asyncio
from typing import Any

from backend.app.agents.state import AgentState, Emit
from backend.app.llm.deepseek import DeepSeekClient
from backend.app.services.code_executor import SkillCodeExecutionError
from backend.app.services.skill_loader import SkillSpec


def make_execute_node(llm: DeepSeekClient, emit: Emit, deps: Any):
    async def run_one_skill(skill: SkillSpec, state: AgentState) -> dict[str, Any]:
        await emit("progress", 5, f"Running Skill: {skill.name}", {"agent": skill.name, "agent_state": "running"})
        try:
            skill_output = await deps.run_tool(
                skill.name,
                lambda: deps.execute_skill(
                    state["message"],
                    skill,
                    llm,
                    emit,
                    history=state.get("history", []),
                    attachments=state.get("attachments", []),
                    data_profiles=state.get("data_profiles", []),
                ),
                policy=deps.ToolRetryPolicy(max_attempts=1, wrap_exceptions=False),
            )
            skill_output = deps.enrich_skill_output_with_id_mapping(skill_output)
            evaluation = await deps.evaluate_skill_result(
                user_message=state["message"],
                resolved_message=state["message"],
                skill=skill,
                result=skill_output.get("result"),
                llm=llm,
            )
        except Exception as exc:
            return await handle_first_failure(skill, state, exc)

        if evaluation.get("category") == "retry_code" and skill.execution_mode.startswith("deterministic"):
            skill_output["evaluation"] = {
                **evaluation,
                "category": "partial",
                "retry_instruction": "",
            }
            await emit("progress", 5, f"Skill Completed: {skill.name}", {"agent": skill.name, "agent_state": "done"})
            return {"skill_name": skill.name, "output": skill_output}

        if evaluation.get("category") == "retry_code":
            retry_result = await retry_after_partial_result(skill, state, skill_output, evaluation)
            if retry_result is not None:
                return retry_result
        skill_output["evaluation"] = evaluation
        await emit("progress", 5, f"Skill Completed: {skill.name}", {"agent": skill.name, "agent_state": "done"})
        return {"skill_name": skill.name, "output": skill_output}

    async def handle_first_failure(skill: SkillSpec, state: AgentState, exc: Exception) -> dict[str, Any]:
        first_error = str(exc)
        first_code = exc.code if isinstance(exc, SkillCodeExecutionError) else None
        evaluation = await deps.evaluate_skill_result(
            user_message=state["message"],
            resolved_message=state["message"],
            skill=skill,
            result=None,
            llm=llm,
            error=first_error,
        )
        if evaluation.get("category") != "retry_code" or skill.execution_mode.startswith("deterministic"):
            await emit("progress", 5, f"Skill Completed: {skill.name}", {"agent": skill.name, "agent_state": "done"})
            return {
                "skill_name": skill.name,
                "output": {
                    "mode": "execution_failed",
                    "result": None,
                    "error": first_error,
                    "code": first_code,
                    "evaluation": evaluation,
                },
            }
        await emit(
            "progress",
            5,
            f"Retrying Skill: {skill.name}",
            {"agent": skill.name, "agent_state": "running", "retry": True, "reason": evaluation.get("reason")},
        )
        try:
            skill_output = await deps.retry_skill(
                state["message"],
                skill,
                llm,
                previous_code=first_code,
                previous_error=first_error,
                evaluation=evaluation,
                emit=emit,
                history=state.get("history", []),
            )
            skill_output = deps.enrich_skill_output_with_id_mapping(skill_output)
        except Exception as retry_exc:
            retry_code = retry_exc.code if isinstance(retry_exc, SkillCodeExecutionError) else None
            await emit("progress", 5, f"Skill Completed: {skill.name}", {"agent": skill.name, "agent_state": "done"})
            return {
                "skill_name": skill.name,
                "output": {
                    "mode": "retry_failed",
                    "result": None,
                    "error": str(retry_exc),
                    "code": retry_code,
                    "evaluation": {
                        "category": "not_found",
                        "answered": False,
                        "reason": "重试后仍未能得到可用结果",
                        "missing": ["valid_skill_result"],
                    },
                },
            }
        evaluation = await deps.evaluate_skill_result(
            user_message=state["message"],
            resolved_message=state["message"],
            skill=skill,
            result=skill_output.get("result"),
            llm=llm,
        )
        skill_output["evaluation"] = evaluation
        await emit("progress", 5, f"Skill Completed: {skill.name}", {"agent": skill.name, "agent_state": "done"})
        return {"skill_name": skill.name, "output": skill_output}

    async def retry_after_partial_result(
        skill: SkillSpec,
        state: AgentState,
        skill_output: dict[str, Any],
        evaluation: dict[str, Any],
    ) -> dict[str, Any] | None:
        await emit(
            "progress",
            5,
            f"Retrying Skill: {skill.name}",
            {"agent": skill.name, "agent_state": "running", "retry": True, "reason": evaluation.get("reason")},
        )
        try:
            retried_output = await deps.retry_skill(
                state["message"],
                skill,
                llm,
                previous_code=skill_output.get("code"),
                previous_result=skill_output.get("result"),
                evaluation=evaluation,
                emit=emit,
                history=state.get("history", []),
            )
            retried_output = deps.enrich_skill_output_with_id_mapping(retried_output)
        except Exception as retry_exc:
            retry_code = retry_exc.code if isinstance(retry_exc, SkillCodeExecutionError) else None
            await emit("progress", 5, f"Skill Completed: {skill.name}", {"agent": skill.name, "agent_state": "done"})
            return {
                "skill_name": skill.name,
                "output": {
                    "mode": "retry_failed",
                    "result": skill_output.get("result"),
                    "error": str(retry_exc),
                    "code": retry_code,
                    "evaluation": {
                        "category": "partial",
                        "answered": False,
                        "reason": "第一次结果不足，重试后仍未能得到更好的可用结果",
                        "missing": ["valid_retry_result"],
                    },
                },
            }
        evaluation = await deps.evaluate_skill_result(
            user_message=state["message"],
            resolved_message=state["message"],
            skill=skill,
            result=retried_output.get("result"),
            llm=llm,
        )
        retried_output["evaluation"] = evaluation
        await emit("progress", 5, f"Skill Completed: {skill.name}", {"agent": skill.name, "agent_state": "done"})
        return {"skill_name": skill.name, "output": retried_output}

    async def execute_node(state: AgentState) -> AgentState:
        skills = state.get("skills", [])
        if not skills:
            return {"skill_outputs": []}
        if len(skills) == 1:
            item = await run_one_skill(skills[0], state)
            return {"skill_output": item["output"], "skill_outputs": [item]}
        outputs = await asyncio.gather(*(run_one_skill(skill, state) for skill in skills))
        return {"skill_output": outputs[0]["output"], "skill_outputs": list(outputs)}

    return execute_node

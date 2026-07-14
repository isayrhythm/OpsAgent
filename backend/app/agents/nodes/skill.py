from __future__ import annotations

import asyncio
from typing import Any

from backend.app.agents.background_result import (
    background_run_artifacts,
    background_run_title,
    format_background_skill_answer,
)
from backend.app.agents.state import AgentState, Emit
from backend.app.llm.deepseek import DeepSeekClient
from backend.app.services.code_executor import SkillCodeExecutionError
from backend.app.services.skill_loader import SkillSpec


BACKGROUND_SKILLS = {
    "differential_protein_analysis",
    "differential_transcriptomics_analysis",
}


def make_execute_node(llm: DeepSeekClient, emit: Emit, deps: Any):
    async def start_background_skill(skill: SkillSpec, state: AgentState) -> tuple[dict[str, Any], dict[str, Any]]:
        await emit(
            "progress",
            5,
            f"Starting Background Run: {skill.name}",
            {"agent": skill.name, "agent_state": "queued", "background": True},
        )

        async def worker(run_emit: Emit) -> dict[str, Any]:
            worker_llm = deps.llm_factory()
            try:
                skill_output = await deps.run_tool(
                    skill.name,
                    lambda: deps.execute_skill(
                        state["message"],
                        skill,
                        worker_llm,
                        run_emit,
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
                    llm=worker_llm,
                )
                skill_output["evaluation"] = evaluation
            except Exception as exc:
                skill_output = {
                    "mode": "execution_failed",
                    "result": {"error": str(exc)},
                    "error": str(exc),
                }
            result = skill_output.get("result") if isinstance(skill_output, dict) else None
            failed = not isinstance(result, dict) or bool(result.get("error"))
            return {
                "run_status": "failed" if failed else "completed",
                "skill_name": skill.name,
                "skill_output": skill_output,
                "answer": format_background_skill_answer(skill.name, skill_output),
                "artifacts": background_run_artifacts(skill_output),
                "usage": worker_llm.usage_snapshot(),
            }

        run = deps.run_manager.create_run(
            user_id=state.get("user_id") or "default",
            session_id=state.get("session_id"),
            run_type="omics_analysis",
            agent=skill.name,
            title=background_run_title(skill.name),
            worker=worker,
        )
        summary = run.summary()
        output = {
            "mode": "background_run",
            "result": {
                "status": "running",
                "analysis": skill.name,
                "background_run": summary,
            },
        }
        return {"skill_name": skill.name, "output": output}, summary

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

        async def dispatch(skill: SkillSpec) -> tuple[dict[str, Any], dict[str, Any] | None]:
            if deps.run_manager is not None and skill.name in BACKGROUND_SKILLS:
                return await start_background_skill(skill, state)
            return await run_one_skill(skill, state), None

        dispatched = await asyncio.gather(*(dispatch(skill) for skill in skills))
        outputs = [item for item, _run in dispatched]
        background_runs = [run for _item, run in dispatched if run is not None]
        return {
            "skill_output": outputs[0]["output"],
            "skill_outputs": outputs,
            "background_runs_created": background_runs,
        }

    return execute_node

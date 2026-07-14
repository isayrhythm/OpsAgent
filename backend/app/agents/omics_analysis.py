from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from backend.app.agents.state import Emit
from backend.app.schemas import UploadedFileSummary


class OmicsAnalysisState(TypedDict, total=False):
    decision: str
    outcome: str
    result: dict[str, Any]
    decisions: list[dict[str, Any]]
    quality_control: dict[str, Any]


ANALYSIS_AGENTS = {
    "proteomics": "Proteomics Analysis Agent",
    "transcriptomics": "Transcriptomics Analysis Agent",
}


async def run_omics_analysis_graph(
    *,
    data_family: str,
    attachments: list[UploadedFileSummary],
    arguments: dict[str, Any],
    data_profiles: list[dict[str, Any]],
    runner: Callable[[list[UploadedFileSummary], dict[str, Any]], dict[str, Any]],
    emit: Emit | None = None,
) -> dict[str, Any]:
    """Run one omics analysis behind an internal decision graph."""

    agent = ANALYSIS_AGENTS[data_family]

    async def assess_input(_state: OmicsAnalysisState) -> OmicsAnalysisState:
        await _emit(emit, 1, f"{agent}: checking analysis input", agent, "running", "assess_input")
        ready = _has_ready_profile(data_profiles, data_family)
        if data_profiles and not ready:
            result = {
                "error": _profile_mismatch_error(data_family),
                "data_profiles": data_profiles,
            }
            return {
                "decision": "finish",
                "outcome": "needs_input",
                "result": result,
                "decisions": [
                    {
                        "stage": "assess_input",
                        "decision": "reject",
                        "reason": "No high-confidence, analysis-ready expression matrix matched the requested data family.",
                    }
                ],
            }

        requested = arguments.get("comparisons")
        comparison_count = len(requested) if isinstance(requested, list) else 0
        return {
            "decision": "execute",
            "outcome": "running",
            "decisions": [
                {
                    "stage": "assess_input",
                    "decision": "execute",
                    "reason": "The input can be delegated to the deterministic analysis executor.",
                    "requested_comparison_count": comparison_count,
                }
            ],
        }

    async def execute_analysis(state: OmicsAnalysisState) -> OmicsAnalysisState:
        await _emit(emit, 2, f"{agent}: running deterministic analysis", agent, "running", "execute_analysis")
        decisions = list(state.get("decisions", []))
        try:
            result = await asyncio.to_thread(runner, attachments, arguments)
        except Exception as exc:
            result = {"error": str(exc), "exception_type": type(exc).__name__}
            decisions.append(
                {
                    "stage": "execute_analysis",
                    "decision": "failed",
                    "reason": f"The deterministic executor raised {type(exc).__name__}.",
                }
            )
            return {"result": result, "outcome": "failed", "decisions": decisions}

        decisions.append(
            {
                "stage": "execute_analysis",
                "decision": "review",
                "reason": "The executor returned a structured result for quality control.",
            }
        )
        return {"result": result, "outcome": "reviewing", "decisions": decisions}

    async def quality_control(state: OmicsAnalysisState) -> OmicsAnalysisState:
        await _emit(emit, 3, f"{agent}: checking outputs", agent, "running", "quality_control")
        result = state.get("result") or {}
        errors: list[str] = []
        warnings: list[str] = []

        if result.get("error"):
            errors.append(str(result["error"]))
        elif result.get("status") != "completed":
            errors.append("The executor did not report a completed status.")

        comparisons = result.get("comparisons")
        if not result.get("error") and not isinstance(comparisons, list):
            errors.append("The result did not contain a comparison list.")
        elif isinstance(comparisons, list) and not comparisons:
            warnings.append("The analysis completed without comparison summaries.")

        files = result.get("files")
        if not result.get("error") and not isinstance(files, dict):
            errors.append("The result did not contain an output file manifest.")
        elif isinstance(files, dict):
            report_path = files.get("report_html")
            if not files.get("report_url"):
                warnings.append("No report URL was returned.")
            if report_path and not Path(str(report_path)).is_file():
                warnings.append("The report path is not currently available on disk.")

        retry = result.get("retry")
        retry_attempted = bool(isinstance(retry, dict) and retry.get("attempted"))
        outcome = "failed" if errors else ("partial" if warnings else "completed")
        decisions = list(state.get("decisions", []))
        decisions.append(
            {
                "stage": "quality_control",
                "decision": outcome,
                "reason": "Output contract and artifact checks completed.",
                "retry_observed": retry_attempted,
            }
        )
        return {
            "outcome": outcome,
            "decisions": decisions,
            "quality_control": {
                "passed": not errors,
                "errors": errors,
                "warnings": warnings,
                "retry_observed": retry_attempted,
            },
        }

    async def finalize(state: OmicsAnalysisState) -> OmicsAnalysisState:
        outcome = state.get("outcome") or "failed"
        agent_state = "done" if outcome in {"completed", "partial", "needs_input"} else "failed"
        await _emit(emit, 4, f"{agent}: {outcome}", agent, agent_state, "finalize")
        result = dict(state.get("result") or {"error": "The analysis produced no result."})
        result["workflow"] = {
            "agent": agent,
            "data_family": data_family,
            "outcome": outcome,
            "decisions": state.get("decisions", []),
            "quality_control": state.get("quality_control", {}),
        }
        return {"result": result}

    graph = StateGraph(OmicsAnalysisState)
    graph.add_node("assess_input", assess_input)
    graph.add_node("execute_analysis", execute_analysis)
    graph.add_node("quality_control", quality_control)
    graph.add_node("finalize", finalize)
    graph.set_entry_point("assess_input")
    graph.add_conditional_edges(
        "assess_input",
        lambda state: state.get("decision", "finish"),
        {"execute": "execute_analysis", "finish": "finalize"},
    )
    graph.add_edge("execute_analysis", "quality_control")
    graph.add_edge("quality_control", "finalize")
    graph.add_edge("finalize", END)
    final_state = await graph.compile().ainvoke({})
    return final_state["result"]


def _has_ready_profile(data_profiles: list[dict[str, Any]], data_family: str) -> bool:
    return any(
        profile.get("status") == "ready"
        and profile.get("analysis_ready") is True
        and profile.get("confidence") == "high"
        and profile.get("data_family") == data_family
        and profile.get("data_type") == "expression_matrix"
        for profile in data_profiles
    )


def _profile_mismatch_error(data_family: str) -> str:
    if data_family == "proteomics":
        return "上传文件尚未被高置信识别为可分析的蛋白组表达矩阵，不能调用蛋白差异分析。"
    return "上传文件尚未被高置信识别为可分析的转录组 counts 表达矩阵，不能调用转录组差异分析。"


async def _emit(
    emit: Emit | None,
    step: int,
    status: str,
    agent: str,
    agent_state: str,
    stage: str,
) -> None:
    if emit is None:
        return
    await emit(
        "progress",
        step,
        status,
        {"agent": agent, "agent_state": agent_state, "stage": stage},
    )

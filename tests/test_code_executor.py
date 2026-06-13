import asyncio
from pathlib import Path

import pytest

from backend.app.schemas import ChatHistoryMessage
from backend.app.services.code_executor import execute_skill
from backend.app.services.message_context import build_skill_message_with_context
from backend.app.services.skill_loader import SkillSpec
from backend.app.services.skill_runtime import SkillContractError


class OfflineLLM:
    available = False


def make_skill(name: str, execution_mode: str = "generated_python") -> SkillSpec:
    executor = {
        "differential_protein_analysis": "differential_protein_analysis",
        "differential_transcriptomics_analysis": "differential_transcriptomics_analysis",
    }.get(name, "")
    return SkillSpec(
        name=name,
        description=name,
        version="1",
        trigger=name,
        execution_mode=execution_mode,
        data_paths=[],
        path=Path(f"skill/{name}.md"),
        content="",
        executor=executor,
        argument_resolver="differential_analysis_json" if executor else "",
    )


def test_differential_protein_rejects_transcriptomics_profile() -> None:
    skill = make_skill("differential_protein_analysis", execution_mode="deterministic_python_r")

    output = asyncio.run(
        execute_skill(
            "做差异分析",
            skill,
            OfflineLLM(),
            data_profiles=[
                {
                    "status": "profiled",
                    "data_family": "transcriptomics",
                    "data_type": "expression_matrix",
                }
            ],
        )
    )

    assert output["mode"] == "deterministic_analysis"
    assert "不能调用蛋白差异分析" in output["result"]["error"]


def test_differential_transcriptomics_rejects_proteomics_profile() -> None:
    skill = make_skill("differential_transcriptomics_analysis", execution_mode="deterministic_python_r")

    output = asyncio.run(
        execute_skill(
            "做转录组差异分析",
            skill,
            OfflineLLM(),
            data_profiles=[
                {
                    "status": "profiled",
                    "data_family": "proteomics",
                    "data_type": "expression_matrix",
                }
            ],
        )
    )

    assert output["mode"] == "deterministic_analysis"
    assert "不能调用转录组差异分析" in output["result"]["error"]


def test_deterministic_skill_requires_registered_executor() -> None:
    skill = make_skill("deterministic_demo", execution_mode="deterministic_python")

    with pytest.raises(SkillContractError, match="requires a registered executor"):
        asyncio.run(execute_skill("run deterministic demo", skill, OfflineLLM()))


def test_skill_message_context_includes_recent_history_window() -> None:
    message = build_skill_message_with_context(
        "继续查啊",
        [
            ChatHistoryMessage(role="user", content="第 1 条"),
            ChatHistoryMessage(role="assistant", content="第 2 条"),
            ChatHistoryMessage(role="user", content="LOC_Os07g48050 可能跟哪些性状相关？"),
            ChatHistoryMessage(role="assistant", content="上一轮无法执行预测。"),
            ChatHistoryMessage(role="user", content="这个基因的具体信息呢？"),
        ],
    )

    assert "当前用户请求：继续查啊" in message
    assert "上一轮用户请求：这个基因的具体信息呢？" in message
    assert "上一轮助手回复：上一轮无法执行预测。" in message
    assert "最近上下文（最多 8 条" in message
    assert "LOC_Os07g48050" in message


def test_differential_protein_rejects_low_confidence_proteomics_profile() -> None:
    skill = make_skill("differential_protein_analysis", execution_mode="deterministic_python_r")

    output = asyncio.run(
        execute_skill(
            "做差异蛋白分析",
            skill,
            OfflineLLM(),
            data_profiles=[
                {
                    "status": "profiled",
                    "data_family": "proteomics",
                    "data_type": "expression_matrix",
                    "confidence": "low",
                    "analysis_ready": False,
                }
            ],
        )
    )

    assert output["mode"] == "deterministic_analysis"
    assert "高置信" in output["result"]["error"]

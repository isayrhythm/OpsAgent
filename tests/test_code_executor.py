import asyncio
from pathlib import Path

import pytest

from backend.app.services.code_executor import execute_skill
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


def test_gene_expression_local_fallback_returns_records() -> None:
    skill = make_skill("query_gene_expression")

    output = asyncio.run(execute_skill("查询 AT1G00001 在 leaf 的表达量", skill, OfflineLLM()))

    assert output["mode"] == "local_fallback"
    assert output["result"]["count"] == 1
    assert output["result"]["records"][0]["gene_id"] == "AT1G00001"
    assert output["result"]["records"][0]["tissue"] == "leaf"


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

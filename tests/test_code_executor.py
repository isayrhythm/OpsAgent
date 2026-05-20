import asyncio
from pathlib import Path

from backend.app.services.code_executor import execute_skill
from backend.app.services.skill_loader import SkillSpec


class OfflineLLM:
    available = False


def test_gene_expression_local_fallback_returns_records() -> None:
    skill = SkillSpec(
        name="query_gene_expression",
        description="query expression",
        version="1",
        trigger="query expression",
        execution_mode="generated_python",
        data_paths=["data/example_gene_expression.csv"],
        path=Path("skill/gene_expression.md"),
        content="",
    )

    output = asyncio.run(execute_skill("查询 AT1G00001 在 leaf 的表达量", skill, OfflineLLM()))

    assert output["mode"] == "local_fallback"
    assert output["result"]["count"] == 1
    assert output["result"]["records"][0]["gene_id"] == "AT1G00001"
    assert output["result"]["records"][0]["tissue"] == "leaf"

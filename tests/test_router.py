import asyncio
from pathlib import Path

from backend.app.services.router import route_skill
from backend.app.services.skill_loader import SkillSpec


class OfflineLLM:
    available = False


def make_skill(name: str, description: str) -> SkillSpec:
    return SkillSpec(
        name=name,
        description=description,
        version="1",
        trigger=description,
        execution_mode="generated_python",
        data_paths=[],
        path=Path(f"{name}.md"),
        content="",
    )


def test_fallback_routes_gene_expression_request() -> None:
    skill = make_skill("query_gene_expression", "query expression")

    decision = asyncio.run(route_skill("查询 AT1G00001 的表达量", [skill], OfflineLLM()))

    assert decision.skill is skill
    assert decision.skills == [skill]


def test_fallback_keeps_normal_chat_without_skill() -> None:
    skill = make_skill("query_gene_expression", "query expression")

    decision = asyncio.run(route_skill("你好，介绍一下你自己", [skill], OfflineLLM()))

    assert decision.skill is None
    assert decision.skills == []

import asyncio
from pathlib import Path

from backend.app.services.router import route_skill
from backend.app.services.skill_loader import SkillSpec


class OfflineLLM:
    available = False


class Settings:
    router_model = "router"


class MisroutingLLM:
    available = True
    settings = Settings()

    async def chat(self, *args, **kwargs):
        return (
            '{"resolved_message":"解释一下大模型量化模型是什么，包含 FP32、INT4 和 LLM.int8",'
            '"skill_names":["query_gene_info"],"reason":"bad route"}'
        )


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


def test_quantization_explanation_does_not_route_to_gene_info() -> None:
    skills = [
        make_skill("query_gene_expression", "query expression"),
        make_skill("query_gene_info", "query gene info"),
    ]
    message = "解释一下大模型量化模型是什么，涉及 FP32、FP16、INT8、INT4 和 LLM.int8()"

    decision = asyncio.run(route_skill(message, skills, OfflineLLM()))

    assert decision.skill is None
    assert decision.skills == []


def test_llm_gene_info_misroute_is_filtered_for_normal_chat() -> None:
    skill = make_skill("query_gene_info", "query gene info")

    decision = asyncio.run(route_skill("解释一下大模型量化模型是什么", [skill], MisroutingLLM()))

    assert decision.skill is None
    assert decision.skills == []


def test_arabidopsis_expression_does_not_route_to_gene_info() -> None:
    expression = make_skill("query_gene_expression", "query expression")
    gene_info = make_skill("query_gene_info", "query gene info")

    decision = asyncio.run(route_skill("查询 AT1G00001 在 leaf 和 root 的表达量", [gene_info, expression], OfflineLLM()))

    assert decision.skill is expression
    assert decision.skills == [expression]

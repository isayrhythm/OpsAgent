import asyncio
from pathlib import Path

import pytest

from backend.app.schemas import DetachedFileSummary
from backend.app.services.router import route_skill
from backend.app.services.skill_loader import SkillSpec


class Settings:
    router_model = "deepseek-v4-flash"


class OfflineLLM:
    available = False


class FakeRouterLLM:
    available = True
    settings = Settings()

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = []

    async def chat(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


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


def test_model_routes_selected_skill() -> None:
    skill = make_skill("query_gene_expression", "query expression")
    llm = FakeRouterLLM(
        '{"skill_names":["query_gene_expression"],"reason":"needs data"}'
    )

    decision = asyncio.run(route_skill("查询 AT1G00001 的表达量", [skill], llm))

    assert decision.skill is skill
    assert decision.skills == [skill]


def test_model_empty_skill_names_means_normal_chat() -> None:
    skill = make_skill("query_gene_info", "query gene info")
    llm = FakeRouterLLM(
        '{"skill_names":[],"reason":"concept explanation"}'
    )

    decision = asyncio.run(route_skill("解释一下大模型量化模型是什么", [skill], llm))

    assert decision.skill is None
    assert decision.skills == []


def test_model_route_does_not_merge_local_rule_fallback() -> None:
    skill = make_skill("query_gene_info", "query gene info")
    llm = FakeRouterLLM(
        '{"skill_names":[],"reason":"normal chat"}'
    )

    decision = asyncio.run(route_skill("解释一下大模型量化模型是什么", [skill], llm))

    assert decision.skill is None
    assert decision.skills == []


def test_router_unavailable_raises_instead_of_fallback() -> None:
    skill = make_skill("query_gene_info", "query gene info")

    with pytest.raises(RuntimeError, match="router model is unavailable"):
        asyncio.run(route_skill("解释一下大模型量化模型是什么", [skill], OfflineLLM()))


def test_invalid_router_json_raises_instead_of_fallback() -> None:
    skill = make_skill("query_gene_info", "query gene info")
    llm = FakeRouterLLM("not json")

    with pytest.raises(RuntimeError, match="invalid JSON"):
        asyncio.run(route_skill("解释一下大模型量化模型是什么", [skill], llm))


def test_router_includes_data_profiles_for_skill_selection() -> None:
    skill = make_skill("differential_protein_analysis", "differential protein analysis")
    llm = FakeRouterLLM(
        '{"skill_names":["differential_protein_analysis"],"reason":"proteomics matrix"}'
    )
    profiles = [
        {
            "filename": "matrix.csv",
            "data_family": "proteomics",
            "data_type": "expression_matrix",
            "recommended_skills": ["differential_protein_analysis"],
        }
    ]

    decision = asyncio.run(route_skill("做差异蛋白分析", [skill], llm, data_profiles=profiles))

    assert decision.skill is skill
    request_messages = llm.calls[0][0][0]
    assert "data_profiles" in request_messages[1]["content"]
    assert "proteomics" in request_messages[1]["content"]


def test_router_includes_detached_files_for_current_attachment_state() -> None:
    skill = make_skill("differential_protein_analysis", "differential protein analysis")
    llm = FakeRouterLLM('{"skill_names":[],"reason":"file was removed"}')

    decision = asyncio.run(
        route_skill(
            "继续分析刚才的文件",
            [skill],
            llm,
            detached_files=[DetachedFileSummary(file_id="file-a", filename="removed.csv")],
        )
    )

    assert decision.skill is None
    request_messages = llm.calls[0][0][0]
    assert "detached_files" in request_messages[1]["content"]
    assert "removed.csv" in request_messages[1]["content"]

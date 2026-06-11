import asyncio
import json
from types import SimpleNamespace

from backend.app.agents import deep_research
from backend.app.agents import agent_graph
from backend.app.tools.web_search_planner import SearchPlan, SearchQuery


class OfflineLLM:
    available = False


def test_agent_graph_routes_deep_research_without_ui_blocks(monkeypatch) -> None:
    events = []

    async def capture(event_type, step, status, data=None) -> None:
        events.append((event_type, step, status, data))

    async def fake_search(queries, **_kwargs):
        return {
            "query": queries[0]["query"],
            "results": [
                {
                    "title": "Evidence A",
                    "url": "https://example.com/a",
                    "content": "Evidence summary for the research step.",
                }
            ],
        }

    monkeypatch.setattr(agent_graph, "load_skill_registry", lambda: [])
    monkeypatch.setattr(deep_research, "search_web_queries", fake_search)

    graph = agent_graph.build_agent_graph(OfflineLLM(), capture)
    result = asyncio.run(
        graph.ainvoke(
            {
                "message": "请做一个深度研究：COLD1 基因和耐冷性的关系",
                "history": [],
                "attachments": [],
                "detached_files": [],
                "search": {"mode": "off", "providers": []},
            }
        )
    )

    assert result["research"]["plan"]["tasks"]
    assert result["research"]["tasks"]
    assert result["research"]["sources"][0]["url"] == "https://example.com/a"
    assert "Evidence summary" in result["answer"]
    assert not any(event[0] == "ui_delta" for event in events)
    assert any(
        event[0] == "progress"
        and isinstance(event[3], dict)
        and event[3].get("research_plan", {}).get("steps")
        for event in events
    )
    assert any(
        event[0] == "progress"
        and isinstance(event[3], dict)
        and event[3].get("research_step", {}).get("status") == "completed"
        and "Search Query Rewriter" in event[3].get("research_step", {}).get("tools", [])
        for event in events
    )


def test_deep_research_intent_classifier_uses_explicit_keywords() -> None:
    assert deep_research.should_route_deep_research("帮我深度研究一下水稻耐盐机制")
    assert not deep_research.should_route_deep_research("COLD1 是什么？")


def test_deep_research_final_answer_streams_deltas(monkeypatch) -> None:
    events = []

    class StreamingLLM:
        available = True
        settings = SimpleNamespace(router_model="router", answer_model="answer")
        planner_tools = []

        async def chat(self, messages, **kwargs):
            system = messages[0]["content"]
            if kwargs.get("response_format"):
                if "ResearchPlanner" in system:
                    payload = json.loads(messages[1]["content"])
                    self.planner_tools = payload.get("available_tools") or []
                    return json.dumps(
                        {
                            "summary": "Research COLD1.",
                            "tasks": [
                                {
                                    "id": "T1",
                                    "title": "Collect evidence",
                                    "question": "COLD1 cold tolerance evidence",
                                    "purpose": "Gather direct evidence.",
                                    "dependencies": [],
                                    "tools": ["gene_phenotype_prediction", "Tavily Search"],
                                }
                            ],
                        }
                    )
                if "ResearchEvaluator" in system:
                    return json.dumps({"sufficient": True, "missing": [], "repair_tasks": []})
                return json.dumps(
                    {
                        "deep_research": True,
                        "reason": "needs research",
                        "research_goal": "COLD1 cold tolerance",
                    }
                )
            return "COLD1 evidence summary [1]"

        async def stream_chat(self, messages, **_kwargs):
            yield "Final "
            yield "answer [1]"

    async def capture(event_type, step, status, data=None) -> None:
        events.append((event_type, step, status, data))

    async def fake_plan(*_args, **_kwargs):
        return SearchPlan(
            mode="force",
            need_search=True,
            reason="forced",
            queries=[SearchQuery(query="COLD1 cold tolerance", purpose="evidence")],
        )

    async def fake_search(_queries, **_kwargs):
        return {
            "query": "COLD1 cold tolerance",
            "results": [
                {
                    "title": "COLD1 paper",
                    "url": "https://example.com/cold1",
                    "content": "COLD1 regulates cold tolerance.",
                }
            ],
        }

    fake_skill = SimpleNamespace(
        name="gene_phenotype_prediction",
        description="Predict gene phenotype from known evidence.",
        trigger="Use when the user asks about likely gene phenotype or trait association.",
        execution_mode="deterministic",
    )
    streaming_llm = StreamingLLM()

    monkeypatch.setattr(agent_graph, "load_skill_registry", lambda: [fake_skill])
    monkeypatch.setattr(deep_research, "plan_web_search", fake_plan)
    monkeypatch.setattr(deep_research, "search_web_queries", fake_search)

    graph = agent_graph.build_agent_graph(streaming_llm, capture)
    result = asyncio.run(
        graph.ainvoke(
            {
                "message": "deep research COLD1 gene and rice cold tolerance",
                "history": [],
                "attachments": [],
                "detached_files": [],
                "search": {"mode": "off", "providers": []},
            }
        )
    )

    answer_deltas = [
        event[3].get("delta")
        for event in events
        if event[0] == "answer_delta" and isinstance(event[3], dict)
    ]
    assert result["answer"] == "Final answer [1]"
    assert answer_deltas == ["Final ", "answer [1]"]
    assert any(tool.get("name") == "gene_phenotype_prediction" for tool in streaming_llm.planner_tools)
    assert result["research"]["plan"]["tasks"][0]["tools"] == ["Tavily Search"]


def test_deep_research_plan_trims_overbroad_synthesis_tools() -> None:
    planner = deep_research.ResearchPlanner(OfflineLLM())
    tool_catalog = [
        {"name": "Search Query Rewriter", "type": "search"},
        {"name": "Tavily Search", "type": "search"},
        {"name": "Quark Search", "type": "search"},
        {"name": "trait2gene_query", "type": "skill"},
        {"name": "query_gene_info", "type": "skill"},
        {"name": "gene_phenotype_prediction", "type": "skill"},
    ]
    plan, tasks = planner.validate_plan(
        {
            "summary": "Research salt tolerance.",
            "tools": tool_catalog,
            "tasks": [
                {
                    "id": "T1",
                    "title": "本地数据库查询：耐盐相关基因",
                    "question": "查询水稻耐盐相关基因",
                    "tools": ["trait2gene_query"],
                },
                {
                    "id": "T2",
                    "title": "交叉验证与整合",
                    "question": "整合前面结果并形成候选基因列表",
                    "dependencies": ["T1"],
                    "tools": [tool["name"] for tool in tool_catalog],
                },
            ],
        },
        "深度研究水稻耐盐基因",
        ["tavily", "quark"],
        tool_catalog,
    )

    assert tasks[0].tools == ["trait2gene_query"]
    assert tasks[1].tools == []
    assert plan["tasks"][1]["tools"] == []
    assert plan["tasks"][1]["dependencies"] == ["T1"]


def test_deep_research_plan_links_trait_genes_to_gene_info() -> None:
    planner = deep_research.ResearchPlanner(OfflineLLM())
    tool_catalog = [
        {"name": "trait2gene_query", "type": "skill"},
        {"name": "query_gene_info", "type": "skill"},
    ]
    plan, tasks = planner.validate_plan(
        {
            "summary": "Research salt tolerance genes.",
            "tools": tool_catalog,
            "tasks": [
                {
                    "id": "T1",
                    "title": "Find trait-associated genes",
                    "question": "Find rice salt tolerance genes",
                    "tools": ["trait2gene_query"],
                },
                {
                    "id": "T2",
                    "title": "Get candidate gene details",
                    "question": "Get detailed functions for candidate genes",
                    "tools": ["query_gene_info"],
                },
            ],
        },
        "深度研究水稻耐盐相关基因功能",
        [],
        tool_catalog,
    )

    assert tasks[1].dependencies == ["T1"]
    assert plan["tasks"][1]["dependencies"] == ["T1"]


def test_deep_research_plan_repairs_missing_trait_and_gene_info_steps() -> None:
    planner = deep_research.ResearchPlanner(OfflineLLM())
    tool_catalog = [
        {"name": "Search Query Rewriter", "type": "search"},
        {"name": "Quark Search", "type": "search"},
        {"name": "trait2gene_query", "type": "skill"},
        {"name": "query_gene_info", "type": "skill"},
    ]
    plan, tasks = planner.validate_plan(
        {
            "summary": "Research soybean yield genes.",
            "tools": tool_catalog,
            "tasks": [
                {
                    "id": "T1",
                    "title": "Search public evidence",
                    "question": "Search soybean yield genes",
                    "tools": ["Search Query Rewriter", "Quark Search"],
                }
            ],
        },
        "深度研究大豆产量相关基因有哪些？挑几个候选基因说明功能。",
        ["quark"],
        tool_catalog,
    )

    assert any("trait2gene_query" in task.tools for task in tasks)
    info_task = next(task for task in tasks if "query_gene_info" in task.tools)
    trait_task = next(task for task in tasks if "trait2gene_query" in task.tools)
    assert trait_task.id in info_task.dependencies
    assert any("query_gene_info" in item["tools"] for item in plan["tasks"])


def test_deep_research_passes_dependency_gene_ids_to_downstream_skill(monkeypatch) -> None:
    seen_messages = {}

    async def fake_execute_registered_skill(skill, context):
        seen_messages[skill.name] = context.message
        if skill.name == "trait2gene_query":
            return {
                "mode": "deterministic_query",
                "result": {
                    "matches": [
                        {
                            "genes": [
                                {"gene_id": "AGIS_Os01g01010"},
                                {"gene_id": "AGIS_Os02g02020"},
                            ]
                        }
                    ]
                },
            }
        if skill.name == "query_gene_info":
            return {
                "mode": "deterministic_query",
                "result": {
                    "matches": [
                        {"canonical_id": "AGIS_Os01g01010", "function_summary": "salt response"}
                    ]
                },
            }
        return {"mode": "deterministic_query", "result": {}}

    monkeypatch.setattr(deep_research, "execute_registered_skill", fake_execute_registered_skill)
    executor = deep_research.ResearchExecutor(OfflineLLM())
    tasks = [
        deep_research.ResearchTask(
            id="T1",
            title="Find salt tolerance genes",
            question="Find rice salt tolerance genes",
            tools=["trait2gene_query"],
        ),
        deep_research.ResearchTask(
            id="T2",
            title="Get candidate gene details",
            question="Get detailed info for candidate genes",
            dependencies=["T1"],
            tools=["query_gene_info"],
        ),
    ]
    skills = [
        SimpleNamespace(name="trait2gene_query", executor="trait2gene_query"),
        SimpleNamespace(name="query_gene_info", executor="query_gene_info"),
    ]

    async def capture(_task):
        return None

    asyncio.run(executor.execute_dag(tasks, [], [], skills, capture))

    assert tasks[0].status == "completed"
    assert tasks[1].status == "completed"
    assert "AGIS_Os01g01010" in seen_messages["query_gene_info"]
    assert "AGIS_Os02g02020" in seen_messages["query_gene_info"]

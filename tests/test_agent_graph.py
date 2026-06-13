import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.schemas import ChatHistoryMessage, UploadedFileSummary
from backend.app.agents import agent_graph
from backend.app.services.skill_loader import SkillSpec


class OfflineLLM:
    available = False


class StreamingAnswerLLM:
    available = True
    settings = SimpleNamespace(answer_model="answer")

    async def stream_chat(self, *_args, **_kwargs):
        yield "Research path ready."


async def emit(*_args, **_kwargs) -> None:
    return None


def make_skill(name: str, execution_mode: str) -> SkillSpec:
    return SkillSpec(
        name=name,
        description=name,
        version="1",
        trigger=name,
        execution_mode=execution_mode,
        data_paths=[],
        path=Path(f"skill/{name}.md"),
        content="",
        answer_requirements=[],
    )


def patch_route(monkeypatch, skill: SkillSpec) -> None:
    async def route(*_args, **_kwargs):
        return {
            "skill_name": skill.name,
            "skill_names": [skill.name],
            "skills": [skill],
            "route_reason": "selected",
        }

    monkeypatch.setattr(agent_graph, "route_registered_skills", route)
    monkeypatch.setattr(agent_graph, "load_skill_registry", lambda: [skill])


def test_deterministic_skill_failure_returns_skill_output(monkeypatch) -> None:
    skill = make_skill("deterministic_demo", "deterministic_python_r")
    patch_route(monkeypatch, skill)

    async def execute(*_args, **_kwargs):
        raise RuntimeError("deterministic failed")

    async def evaluate(**_kwargs):
        return {
            "category": "retry_code",
            "answered": False,
            "reason": "failed",
            "missing": ["valid_result"],
        }

    monkeypatch.setattr(agent_graph, "execute_skill", execute)
    monkeypatch.setattr(agent_graph, "evaluate_skill_result", evaluate)

    graph = agent_graph.build_agent_graph(OfflineLLM(), emit)
    result = asyncio.run(graph.ainvoke({"message": "run", "history": [], "attachments": [], "detached_files": []}))

    assert result["skill_output"]["mode"] == "execution_failed"
    assert result["skill_output"]["error"] == "deterministic failed"
    assert result["skill_output"]["evaluation"]["category"] == "retry_code"


def test_generated_skill_failure_retries_once_when_evaluator_requests_it(monkeypatch) -> None:
    skill = make_skill("generated_demo", "generated_python")
    patch_route(monkeypatch, skill)
    calls = {"retry": 0}

    async def execute(*_args, **_kwargs):
        raise RuntimeError("generated failed")

    async def retry(*_args, **_kwargs):
        calls["retry"] += 1
        return {"mode": "generated_code_retry", "result": {"ok": True}}

    async def evaluate(**kwargs):
        if kwargs.get("error"):
            return {
                "category": "retry_code",
                "answered": False,
                "reason": "retry",
                "missing": ["valid_result"],
            }
        return {
            "category": "answer",
            "answered": True,
            "reason": "ok",
            "missing": [],
        }

    monkeypatch.setattr(agent_graph, "execute_skill", execute)
    monkeypatch.setattr(agent_graph, "retry_skill", retry)
    monkeypatch.setattr(agent_graph, "evaluate_skill_result", evaluate)

    graph = agent_graph.build_agent_graph(OfflineLLM(), emit)
    result = asyncio.run(graph.ainvoke({"message": "run", "history": [], "attachments": [], "detached_files": []}))

    assert calls["retry"] == 1
    assert result["skill_output"]["mode"] == "generated_code_retry"
    assert result["skill_output"]["result"] == {"ok": True}


def test_agent_graph_routes_explicit_directory_request_to_command_tool(monkeypatch) -> None:
    monkeypatch.setattr(agent_graph, "load_skill_registry", lambda: [])

    async def route(*_args, **_kwargs):
        skill = agent_graph.command_tool_spec()
        return {
            "skill_name": skill.name,
            "skill_names": [skill.name],
            "skills": [skill],
            "route_reason": "LLM selected command tool",
        }

    async def fake_plan(task, context, history, llm):
        assert task["tools"] == ["Shell Command"]
        return SimpleNamespace(command="pwd && ls -la", reason="list directory")

    async def fake_execute(command, **kwargs):
        assert command == "pwd && ls -la"
        return {
            "status": "completed",
            "analysis": "shell_command",
            "command": command,
            "exit_code": 0,
            "stdout": "/workspace\nREADME.md\n",
            "stderr": "",
        }

    monkeypatch.setattr(agent_graph, "route_registered_skills", route)
    monkeypatch.setattr(agent_graph, "plan_shell_command", fake_plan)
    monkeypatch.setattr(agent_graph, "execute_shell_command", fake_execute)

    graph = agent_graph.build_agent_graph(OfflineLLM(), emit)
    result = asyncio.run(
        graph.ainvoke(
            {
                "message": "看看当前目录",
                "history": [],
                "attachments": [],
                "detached_files": [],
                "search": {"mode": "off", "providers": []},
            }
        )
    )

    assert result["route_mode"] == "command"
    assert result["command_outputs"][0]["output"]["stdout"] == "/workspace\nREADME.md\n"
    assert "README.md" in result["answer"]


def test_agent_graph_does_not_route_command_by_keyword_without_llm(monkeypatch) -> None:
    monkeypatch.setattr(agent_graph, "load_skill_registry", lambda: [])

    graph = agent_graph.build_agent_graph(OfflineLLM(), emit)

    with pytest.raises(RuntimeError, match="router model is unavailable"):
        asyncio.run(
            graph.ainvoke(
                {
                    "message": "看看当前目录",
                    "history": [],
                    "attachments": [],
                    "detached_files": [],
                    "search": {"mode": "off", "providers": []},
                }
            )
        )


def test_research_path_skill_emits_ui_steps(monkeypatch) -> None:
    skill = make_skill("query_gene_function_research_path", "deterministic_python")
    patch_route(monkeypatch, skill)
    events = []

    async def capture(event_type, step, status, data=None) -> None:
        events.append((event_type, step, status, data))

    async def execute(*_args, **_kwargs):
        return {
            "mode": "deterministic_query",
            "result": {
                "ui_blocks": [
                    {
                        "id": "path-hy2",
                        "type": "gene_function_research_path",
                        "gene_id": "HY2",
                        "title": "HY2 paper",
                        "paper_id": "Atha_0",
                        "steps": [{"step": "1", "stage_operation": "Rescue"}],
                    }
                ]
            },
        }

    async def evaluate(**_kwargs):
        return {
            "category": "answer",
            "answered": True,
            "reason": "ok",
            "missing": [],
        }

    monkeypatch.setattr(agent_graph, "execute_skill", execute)
    monkeypatch.setattr(agent_graph, "evaluate_skill_result", evaluate)

    graph = agent_graph.build_agent_graph(StreamingAnswerLLM(), capture)
    asyncio.run(graph.ainvoke({"message": "HY2 path", "history": [], "attachments": [], "detached_files": []}))

    answer_events = [event for event in events if event[0] == "answer_delta"]
    ui_events = [event for event in events if event[0] == "ui_delta"]
    assert answer_events
    assert events.index(answer_events[-1]) < events.index(ui_events[0])
    assert ui_events[0][3]["action"] == "start"
    assert ui_events[1][3]["action"] == "step"
    assert ui_events[1][3]["step"]["stage_operation"] == "Rescue"


def test_trait2gene_composite_question_sends_literature_evidence_to_final_llm(monkeypatch) -> None:
    class CaptureFinalLLM:
        available = True
        settings = SimpleNamespace(answer_model="answer")

        def __init__(self) -> None:
            self.calls = []

        async def stream_chat(self, messages, *_args, **_kwargs):
            self.calls.append(messages)
            yield "LLM 整理后的回答，含 Paper A 和 Paper B。"

    skill = make_skill("trait2gene_query", "deterministic_python")
    patch_route(monkeypatch, skill)
    events = []
    message = "大豆产量相关的基因有哪些？水稻株高相关的基因有哪些？"

    async def capture(event_type, step, status, data=None) -> None:
        events.append((event_type, step, status, data))

    async def execute(*_args, **_kwargs):
        return {
            "mode": "deterministic_query",
            "result": {
                "status": "completed",
                "analysis": "trait2gene_query",
                "query": message,
                "matches": [
                    {
                        "species": "soy",
                        "species_label": "soybean",
                        "categories": ["100-seed weight"],
                        "match_mode": "any",
                        "total_genes": 1,
                        "returned_genes": 1,
                        "references": ["Paper A"],
                        "genes": [
                            {
                                "gene_id": "S1",
                                "gene_names": ["SW1"],
                                "categories": ["100-seed weight"],
                                "evidence_count": 1,
                                "references": ["Paper A"],
                                "evidence": [
                                    {
                                        "category": "100-seed weight",
                                        "trait": "seed weight trait",
                                        "literature": "Paper A",
                                        "source": "pubmed",
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "species": "rice",
                        "species_label": "rice",
                        "categories": ["plant height"],
                        "match_mode": "all",
                        "total_genes": 1,
                        "returned_genes": 1,
                        "references": ["Paper B"],
                        "genes": [
                            {
                                "gene_id": "G1",
                                "gene_names": ["PH1"],
                                "categories": ["plant height"],
                                "evidence_count": 1,
                                "references": ["Paper B"],
                                "evidence": [
                                    {
                                        "category": "plant height",
                                        "trait": "plant stature",
                                        "literature": "Paper B",
                                        "source": "RAP-DB",
                                    }
                                ],
                            }
                        ],
                    },
                ],
            },
        }

    async def evaluate(**_kwargs):
        return {
            "category": "answer",
            "answered": True,
            "reason": "ok",
            "missing": [],
        }

    monkeypatch.setattr(agent_graph, "execute_skill", execute)
    monkeypatch.setattr(agent_graph, "evaluate_skill_result", evaluate)

    llm = CaptureFinalLLM()
    graph = agent_graph.build_agent_graph(llm, capture)
    result = asyncio.run(
        graph.ainvoke(
            {
                "message": message,
                "history": [],
                "attachments": [],
                "detached_files": [],
            }
        )
    )

    answer_events = [event for event in events if event[0] == "answer_delta"]
    final_payload = llm.calls[0][1]["content"]
    assert result["answer"] == "LLM 整理后的回答，含 Paper A 和 Paper B。"
    assert answer_events[-1][3]["delta"] == result["answer"]
    assert message in final_payload
    assert "Paper A" in final_payload
    assert "Paper B" in final_payload
    assert "evidence" in final_payload


def test_web_search_mode_keeps_skill_router_and_adds_search_context(monkeypatch) -> None:
    class SearchLLM:
        available = True
        settings = SimpleNamespace(answer_model="answer")

        def __init__(self) -> None:
            self.calls = []

        async def stream_chat(self, messages, *_args, **_kwargs):
            self.calls.append(messages)
            yield "searched answer"

    route_calls = {"count": 0}

    async def route(*_args, **_kwargs):
        route_calls["count"] += 1
        return {"skill_name": None, "skill_names": [], "skills": [], "route_reason": "normal chat"}

    async def search(queries, **_kwargs):
        return {
            "query": queries[0]["query"],
            "results": [
                {
                    "title": "Example result",
                    "url": "https://example.com/result",
                    "content": "fresh web context",
                }
            ],
        }

    events = []

    async def capture(event_type, step, status, data=None) -> None:
        events.append((event_type, step, status, data))

    monkeypatch.setattr(agent_graph, "route_registered_skills", route)
    monkeypatch.setattr(agent_graph, "load_skill_registry", lambda: [])
    monkeypatch.setattr(agent_graph, "search_web_queries", search)

    llm = SearchLLM()
    graph = agent_graph.build_agent_graph(llm, capture)
    result = asyncio.run(
        graph.ainvoke(
            {
                "message": "latest Tavily news",
                "history": [],
                "attachments": [],
                "detached_files": [],
                "web_search": True,
                "web_search_mode": "force",
            }
        )
    )

    joined_context = "\n".join(message["content"] for message in llm.calls[0])
    assert result["answer"] == "searched answer"
    assert route_calls["count"] == 1
    assert result["search"]["sources"] == [
        {"index": 1, "title": "Example result", "url": "https://example.com/result"}
    ]
    assert "fresh web context" in joined_context
    assert "https://example.com/result" in joined_context
    assert any(event[2] == "正在搜索网页" for event in events)
    assert any(event[0] == "source_delta" and event[3]["sources"][0]["index"] == 1 for event in events)


def test_web_search_without_sources_still_uses_final_answer_prompt(monkeypatch) -> None:
    class SearchLLM:
        available = True
        settings = SimpleNamespace(answer_model="answer")

        def __init__(self) -> None:
            self.calls = []

        async def stream_chat(self, messages, *_args, **_kwargs):
            self.calls.append(messages)
            yield "searched answer"

    async def route(*_args, **_kwargs):
        return {"skill_name": None, "skill_names": [], "skills": [], "route_reason": "normal chat"}

    async def search(queries, **_kwargs):
        return {"query": queries[0]["query"], "results": []}

    monkeypatch.setattr(agent_graph, "route_registered_skills", route)
    monkeypatch.setattr(agent_graph, "load_skill_registry", lambda: [])
    monkeypatch.setattr(agent_graph, "search_web_queries", search)

    llm = SearchLLM()
    graph = agent_graph.build_agent_graph(llm, emit)
    asyncio.run(
        graph.ainvoke(
            {
                "message": "latest news",
                "history": [],
                "attachments": [],
                "detached_files": [],
                "web_search": True,
                "web_search_mode": "force",
            }
        )
    )

    assert llm.calls[0][0]["content"].startswith("根据当前用户提问")
    assert "web_search" in llm.calls[0][1]["content"]


def test_phenotype_prediction_result_keeps_predictions_for_final_answer(monkeypatch) -> None:
    class CaptureLLM:
        available = True
        settings = SimpleNamespace(answer_model="answer")

        def __init__(self) -> None:
            self.calls = []

        async def stream_chat(self, messages, *_args, **_kwargs):
            self.calls.append(messages)
            yield "phenotype answer"

    skill = make_skill("gene_phenotype_prediction", "deterministic_python")
    skill = SkillSpec(
        name=skill.name,
        description=skill.description,
        version=skill.version,
        trigger=skill.trigger,
        execution_mode=skill.execution_mode,
        data_paths=skill.data_paths,
        path=skill.path,
        content=skill.content,
        answer_requirements=["Present phenotype predictions in a concise table."],
    )
    patch_route(monkeypatch, skill)

    async def execute(*_args, **_kwargs):
        return {
            "mode": "deterministic_query",
            "result": {
                "status": "completed",
                "analysis": "gene_phenotype_prediction",
                "top_k": 1,
                "species_searched": ["rice"],
                "genes": ["AGIS_Os07g043560"],
                "gene_mappings": [
                    {
                        "input": "LOC_Os07g48050",
                        "species": "rice",
                        "species_label": "水稻",
                        "canonical_id": "AGIS_Os07g043560",
                        "query_id": "agis_os07g043560",
                        "matched_by": "gene_trans",
                    }
                ],
                "matches": [
                    {
                        "input": "LOC_Os07g48050",
                        "species": "rice",
                        "species_label": "水稻",
                        "canonical_id": "AGIS_Os07g043560",
                        "matched_by": "gene_trans",
                        "top_k": 1,
                        "predictions": [
                            {
                                "rank": 1,
                                "phenotype": "rice_blast_resistance",
                                "pred_score": 0.089,
                            }
                        ],
                        "source_file": "large.parquet",
                    }
                ],
                "not_found": [],
            },
        }

    async def evaluate(**_kwargs):
        return {
            "category": "answer",
            "answered": True,
            "reason": "ok",
            "missing": [],
        }

    monkeypatch.setattr(agent_graph, "execute_skill", execute)
    monkeypatch.setattr(agent_graph, "evaluate_skill_result", evaluate)

    llm = CaptureLLM()
    graph = agent_graph.build_agent_graph(llm, emit)
    asyncio.run(
        graph.ainvoke(
            {
                "message": "LOC_Os07g48050 可能跟哪些性状相关？",
                "history": [],
                "attachments": [],
                "detached_files": [],
            }
        )
    )

    payload = llm.calls[0][1]["content"]
    assert "rice_blast_resistance" in payload
    assert "gene_mappings" in payload
    assert "id_mapping_summary" in payload
    assert "Present phenotype predictions in a concise table." in payload
    assert "ID mapping applied: LOC_Os07g48050 -> AGIS_Os07g043560" in payload
    assert "AGIS_Os07g043560" in payload
    assert "<truncated>" not in payload


def test_generic_compaction_keeps_mutant_records_for_final_answer(monkeypatch) -> None:
    class CaptureLLM:
        available = True
        settings = SimpleNamespace(answer_model="answer")

        def __init__(self) -> None:
            self.calls = []

        async def stream_chat(self, messages, *_args, **_kwargs):
            self.calls.append(messages)
            yield "mutant answer"

    skill = make_skill("gene_mutant_query", "deterministic_python")
    patch_route(monkeypatch, skill)

    async def execute(*_args, **_kwargs):
        return {
            "mode": "deterministic_query",
            "result": {
                "status": "completed",
                "analysis": "gene_mutant_query",
                "query": "LOC_Os04g54860 有突变体吗？",
                "record_limit": 30,
                "species_searched": ["rice"],
                "genes": ["LOC_Os04g54860"],
                "gene_mappings": [
                    {
                        "input": "LOC_Os04g54860",
                        "species": "rice",
                        "species_label": "rice",
                        "canonical_id": "LOC_Os04g54860",
                        "query_id": "LOC_Os04g54860",
                        "matched_by": "gene_trans",
                    }
                ],
                "matches": [
                    {
                        "input": "LOC_Os04g54860",
                        "species": "rice",
                        "species_label": "rice",
                        "database": "BGBIO",
                        "canonical_id": "LOC_Os04g54860",
                        "query_id": "LOC_Os04g54860",
                        "matched_by": "gene_trans",
                        "has_mutant": True,
                        "total_hits": 7,
                        "returned_records": 1,
                        "records": [
                            {
                                "database": "BGBIO",
                                "species": "rice",
                                "germplasm_type": "突变体种子",
                                "species_or_variety": "水稻/ZH11",
                                "vector": "BGK03",
                                "gene_id": "LOC_Os04g54860",
                                "target_sequence": "GCAGTGGATGCAGGCTGATACGG",
                                "validation": "纯合突变，G缺失",
                            }
                        ],
                    }
                ],
                "not_found": [],
            },
        }

    async def evaluate(**_kwargs):
        return {
            "category": "answer",
            "answered": True,
            "reason": "ok",
            "missing": [],
        }

    monkeypatch.setattr(agent_graph, "execute_skill", execute)
    monkeypatch.setattr(agent_graph, "evaluate_skill_result", evaluate)

    llm = CaptureLLM()
    graph = agent_graph.build_agent_graph(llm, emit)
    asyncio.run(
        graph.ainvoke(
            {
                "message": "LOC_Os04g54860 有突变体吗？",
                "history": [],
                "attachments": [],
                "detached_files": [],
            }
        )
    )

    payload = llm.calls[0][1]["content"]
    assert "LOC_Os04g54860" in payload
    assert "id_mapping_summary" in payload
    assert "ID mapping applied: LOC_Os04g54860 -> LOC_Os04g54860" in payload
    assert "纯合突变，G缺失" in payload
    assert "GCAGTGGATGCAGGCTGATACGG" in payload
    assert "<truncated>" not in payload


def test_registered_skill_receives_recent_history(monkeypatch) -> None:
    skill = make_skill("gene_phenotype_prediction", "deterministic_python")
    skill = SkillSpec(
        name=skill.name,
        description=skill.description,
        version=skill.version,
        trigger=skill.trigger,
        execution_mode=skill.execution_mode,
        data_paths=skill.data_paths,
        path=skill.path,
        content=skill.content,
        executor="gene_phenotype_prediction",
        argument_resolver="message",
    )
    patch_route(monkeypatch, skill)
    captured = {}

    async def execute(message, selected_skill, llm, emit=None, **kwargs):
        captured["history"] = kwargs.get("history")
        return {
            "mode": "deterministic_query",
            "result": {
                "status": "completed",
                "analysis": "gene_phenotype_prediction",
                "matches": [],
                "not_found": [],
            },
        }

    async def evaluate(**_kwargs):
        return {
            "category": "answer",
            "answered": True,
            "reason": "ok",
            "missing": [],
        }

    monkeypatch.setattr(agent_graph, "execute_skill", execute)
    monkeypatch.setattr(agent_graph, "evaluate_skill_result", evaluate)

    graph = agent_graph.build_agent_graph(StreamingAnswerLLM(), emit)
    asyncio.run(
        graph.ainvoke(
            {
                "message": "继续查啊",
                "history": [ChatHistoryMessage(role="user", content="LOC_Os07g48050 可能跟哪些性状相关？")],
                "attachments": [],
                "detached_files": [],
            }
        )
    )

    assert captured["history"][0].content == "LOC_Os07g48050 可能跟哪些性状相关？"


def test_attachment_chat_prompt_exposes_intake_warnings_and_action_guard(monkeypatch) -> None:
    class ChatLLM:
        available = True
        settings = SimpleNamespace(answer_model="answer")

        def __init__(self) -> None:
            self.calls = []

        async def stream_chat(self, messages, *_args, **_kwargs):
            self.calls.append(messages)
            yield "grounded file answer"

    async def route(*_args, **_kwargs):
        return {"skill_name": None, "skill_names": [], "skills": [], "route_reason": "inspect intake summary"}

    monkeypatch.setattr(agent_graph, "route_registered_skills", route)
    monkeypatch.setattr(agent_graph, "load_skill_registry", lambda: [])
    attachment = UploadedFileSummary(
        file_id="rank",
        filename="rank.csv",
        content_type="text/csv",
        size=12,
        path="rank.csv",
        intake={
            "status": "ready",
            "intake_version": 4,
            "data_family": "expression",
            "data_type": "expression_matrix",
            "confidence": "low",
            "analysis_ready": False,
            "warnings": ["可能是性状表。"],
        },
    )

    llm = ChatLLM()
    graph = agent_graph.build_agent_graph(llm, emit)
    asyncio.run(
        graph.ainvoke(
            {
                "message": "我上传了文件，下一步怎么处理",
                "history": [],
                "attachments": [attachment],
                "detached_files": [],
            }
        )
    )

    assert "不要声称已经读取文件" in llm.calls[0][0]["content"]
    assert "识别警告" in llm.calls[0][1]["content"]
    assert "可能是性状表" in llm.calls[0][1]["content"]

import asyncio
from pathlib import Path

from backend.app.services import agent_graph
from backend.app.services.router import RouteDecision
from backend.app.services.skill_loader import SkillSpec


class OfflineLLM:
    available = False


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
    )


def patch_route(monkeypatch, skill: SkillSpec) -> None:
    async def route(*_args, **_kwargs) -> RouteDecision:
        return RouteDecision(skill=skill, skills=[skill], reason="selected")

    monkeypatch.setattr(agent_graph, "route_skill", route)
    monkeypatch.setattr(agent_graph, "load_skill_catalog", lambda: [skill])
    monkeypatch.setattr(agent_graph, "load_skill", lambda _path: skill)


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

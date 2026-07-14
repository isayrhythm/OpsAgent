import asyncio
from pathlib import Path

from backend.app.agents import agent_graph
from backend.app.runs.manager import RunManager
from backend.app.services.skill_loader import SkillSpec


class OfflineLLM:
    available = False


class BackgroundLLM:
    available = False

    def usage_snapshot(self):
        return {"total_tokens": 0}


async def emit(*_args, **_kwargs) -> None:
    return None


def test_omics_skill_is_detached_into_background_run(monkeypatch) -> None:
    skill = SkillSpec(
        name="differential_protein_analysis",
        description="protein analysis",
        version="1",
        trigger="protein analysis",
        execution_mode="deterministic_python_r",
        data_paths=[],
        path=Path("skill/differential_protein_analysis.md"),
        content="",
    )

    async def route(**_kwargs):
        return {
            "skill_name": skill.name,
            "skill_names": [skill.name],
            "skills": [skill],
            "route_reason": "selected",
        }

    async def execute(*_args, **_kwargs):
        await asyncio.sleep(0)
        return {
            "mode": "deterministic_analysis",
            "result": {
                "status": "completed",
                "analysis": skill.name,
                "parameters": {"pvalue_cutoff": 0.05, "fold_change_cutoff": 1.5},
                "comparisons": [{"comparison": "MT vs WT", "differential": 2, "up": 1, "down": 1}],
                "files": {"report_url": "/api/artifacts/run/report.html"},
            },
        }

    async def evaluate(**_kwargs):
        return {"category": "answer", "answered": True, "reason": "ok", "missing": []}

    monkeypatch.setattr(agent_graph, "load_skill_registry", lambda: [skill])
    monkeypatch.setattr(agent_graph, "route_registered_skills", route)
    monkeypatch.setattr(agent_graph, "execute_skill", execute)
    monkeypatch.setattr(agent_graph, "evaluate_skill_result", evaluate)
    monkeypatch.setattr(agent_graph, "DeepSeekClient", BackgroundLLM)

    async def run() -> None:
        manager = RunManager(retention_seconds=-1)
        graph = agent_graph.build_agent_graph(OfflineLLM(), emit, manager)
        result = await graph.ainvoke(
            {
                "user_id": "user-a",
                "session_id": "session-a",
                "message": "run proteomics",
                "history": [],
                "attachments": [],
                "detached_files": [],
                "search": {"mode": "off", "providers": []},
                "active_runs": [],
            }
        )

        assert result["skill_output"]["mode"] == "background_run"
        assert "继续聊天" in result["answer"]
        summary = result["background_runs_created"][0]
        state = manager.get(summary["run_id"])
        await state.runner
        assert state.status == "completed"
        assert "MT vs WT" in state.result["answer"]

    asyncio.run(run())

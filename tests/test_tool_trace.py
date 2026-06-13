from backend.app.agents.tool_trace import build_tool_trace, format_tool_trace_context
from backend.app.schemas import TaskEvent


def test_tool_trace_summarizes_progress_events_and_results() -> None:
    trace = build_tool_trace(
        [
            TaskEvent(
                type="progress",
                step=2,
                status="Reading File Context",
                data={"agent": "File Inspector", "agent_state": "running", "files": ["genes.csv"]},
            ),
            TaskEvent(
                type="progress",
                step=5,
                status="Running Skill: query_gene_info",
                data={"agent": "query_gene_info", "agent_state": "running"},
            ),
            TaskEvent(
                type="progress",
                step=5,
                status="Skill Completed: query_gene_info",
                data={"agent": "query_gene_info", "agent_state": "done"},
            ),
            TaskEvent(type="answer_delta", step=7, status="Streaming Answer", data={"delta": "answer"}),
        ],
        {
            "skill_output": {
                "skill_name": "query_gene_info",
                "result": {"matches": [{"gene_id": "LOC_Os07g48050"}]},
            }
        },
    )

    context = format_tool_trace_context(trace)

    assert trace["tools"][0]["name"] == "File Inspector"
    assert trace["tools"][0]["input_summary"] == "files: genes.csv"
    assert "query_gene_info [skill]: completed" in context
    assert "LOC_Os07g48050" in context

import asyncio

from backend.app.agents.omics_analysis import run_omics_analysis_graph


def ready_profile(data_family: str) -> dict:
    return {
        "status": "ready",
        "analysis_ready": True,
        "confidence": "high",
        "data_family": data_family,
        "data_type": "expression_matrix",
    }


def test_omics_graph_rejects_mismatched_profile_before_executor() -> None:
    calls = {"runner": 0}

    def runner(_attachments, _arguments):
        calls["runner"] += 1
        return {}

    result = asyncio.run(
        run_omics_analysis_graph(
            data_family="proteomics",
            attachments=[],
            arguments={},
            data_profiles=[ready_profile("transcriptomics")],
            runner=runner,
        )
    )

    assert calls["runner"] == 0
    assert "error" in result
    assert result["workflow"]["outcome"] == "needs_input"
    assert result["workflow"]["decisions"][0]["decision"] == "reject"


def test_omics_graph_executes_and_records_quality_control(tmp_path) -> None:
    report = tmp_path / "report.html"
    report.write_text("ok", encoding="utf-8")
    events = []

    async def emit(event_type, step, status, data=None):
        events.append((event_type, step, status, data))

    def runner(_attachments, arguments):
        assert arguments["comparisons"] == [{"numerator": "MT", "denominator": "WT"}]
        return {
            "status": "completed",
            "analysis": "differential_transcriptomics_analysis",
            "parameters": {"padj_cutoff": 0.05, "log2_fc_cutoff": 1.0},
            "comparisons": [{"comparison": "MT vs WT", "significant": 3, "up": 2, "down": 1}],
            "files": {"report_html": str(report), "report_url": "/api/artifacts/run/report.html"},
        }

    result = asyncio.run(
        run_omics_analysis_graph(
            data_family="transcriptomics",
            attachments=[],
            arguments={"comparisons": [{"numerator": "MT", "denominator": "WT"}]},
            data_profiles=[ready_profile("transcriptomics")],
            runner=runner,
            emit=emit,
        )
    )

    workflow = result["workflow"]
    assert workflow["outcome"] == "completed"
    assert workflow["quality_control"]["passed"] is True
    assert [item[3]["stage"] for item in events] == [
        "assess_input",
        "execute_analysis",
        "quality_control",
        "finalize",
    ]

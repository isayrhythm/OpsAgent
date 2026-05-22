import asyncio
from types import SimpleNamespace

from backend.app.services.differential_arguments import resolve_differential_arguments


class JsonLLM:
    available = True
    settings = SimpleNamespace(router_model="router")

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.response


def ready_profile(data_family: str, groups: dict[str, list[str]]) -> dict[str, object]:
    return {
        "status": "ready",
        "analysis_ready": True,
        "confidence": "high",
        "data_family": data_family,
        "data_type": "expression_matrix",
        "sample_groups": groups,
    }


def test_protein_arguments_use_json_output_and_detected_groups() -> None:
    llm = JsonLLM(
        '{"group_a":"WT","group_b":"MT","pvalue_cutoff":0.01,"fold_change_cutoff":2,"reason":"user requested"}'
    )

    arguments = asyncio.run(
        resolve_differential_arguments(
            "WT 和 MT 做差异蛋白分析，pvalue 改为 0.01，fold change 设为 2",
            "differential_protein_analysis",
            [ready_profile("proteomics", {"WT": ["WT1", "WT2"], "MT": ["MT1", "MT2"]})],
            llm,
        )
    )

    assert arguments == {
        "group_a": "WT",
        "group_b": "MT",
        "pvalue_cutoff": 0.01,
        "fold_change_cutoff": 2.0,
        "reason": "user requested",
    }
    _, kwargs = llm.calls[0]
    assert kwargs["response_format"] == {"type": "json_object"}


def test_transcriptomics_arguments_drop_unknown_comparisons() -> None:
    llm = JsonLLM(
        '{"comparisons":[{"numerator":"MT-D","denominator":"WT-D"},{"numerator":"fake","denominator":"WT-D"}],'
        '"padj_cutoff":0.02,"log2_fc_cutoff":1.3,"reason":"requested"}'
    )

    arguments = asyncio.run(
        resolve_differential_arguments(
            "只跑 MT-D 和 WT-D，FDR 0.02",
            "differential_transcriptomics_analysis",
            [ready_profile("transcriptomics", {"MT-D": ["MT-D1", "MT-D2"], "WT-D": ["WT-D1", "WT-D2"]})],
            llm,
        )
    )

    assert arguments["comparisons"] == [{"numerator": "MT-D", "denominator": "WT-D"}]
    assert arguments["padj_cutoff"] == 0.02
    assert arguments["log2_fc_cutoff"] == 1.3

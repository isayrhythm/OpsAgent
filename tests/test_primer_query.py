import asyncio
import json
from pathlib import Path

import pandas as pd

from backend.app.services import primer_query
from backend.app.services.code_executor import execute_skill
from backend.app.services.primer_query import classify_primer_query, run_primer_query
from backend.app.services.skill_loader import load_skill


class FakeSettings:
    router_model = "fake-router"


class QpcrLLM:
    available = True
    settings = FakeSettings()

    def __init__(self):
        self.kwargs = {}

    async def chat(self, messages, **kwargs):
        self.kwargs = kwargs
        return json.dumps(
            {
                "genes": ["LOC_Os01g66100"],
                "species": ["rice"],
                "primer_sources": ["qpcr"],
                "top_k": 2,
                "reason": "qPCR primers requested",
            }
        )


def write_primer_parquet(path: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_parquet(path, index=False)


def primer_row(gene: str, pair: int, product_length: int) -> dict:
    return {
        "gene": gene,
        "primer_pair": pair,
        "forward_sequence": f"FWD{pair}AACCGGTT",
        "forward_length": 20,
        "forward_tm": 60.1,
        "forward_gc": 55.0,
        "forward_self_complementarity": 0.0,
        "forward_self_3_complementarity": 0.0,
        "reverse_sequence": f"REV{pair}TTGGCCAA",
        "reverse_length": 20,
        "reverse_tm": 60.2,
        "reverse_gc": 50.0,
        "reverse_self_complementarity": 0.0,
        "reverse_self_3_complementarity": 0.0,
        "product_length": product_length,
        "gene_lower": gene.lower(),
    }


def patch_primer_data(monkeypatch, tmp_path: Path) -> None:
    qpcr_path = tmp_path / "qpcr.parquet"
    mutant_path = tmp_path / "mutant.parquet"
    clone_path = tmp_path / "clone.parquet"
    write_primer_parquet(
        qpcr_path,
        [
            primer_row("AGIS_Os01g058220", 1, 195),
            primer_row("AGIS_Os01g058220", 2, 113),
            primer_row("Glyma.01G000100", 1, 155),
        ],
    )
    write_primer_parquet(mutant_path, [primer_row("AGIS_Os01g058220", 1, 703)])
    write_primer_parquet(clone_path, [primer_row("AGIS_Os01g058220", 1, 2981)])
    monkeypatch.setattr(
        primer_query,
        "PRIMER_DATASETS",
        {"qpcr": qpcr_path, "mutant": mutant_path, "clone": clone_path},
    )

    rice_trans = tmp_path / "rice_gene_trans.json"
    soy_trans = tmp_path / "soy_gene_trans.json"
    rice_trans.write_text(json.dumps({"loc_os01g66100": "AGIS_Os01g058220"}), encoding="utf-8")
    soy_trans.write_text(json.dumps({"gmw82.01g000100": "Glyma.01G000100"}), encoding="utf-8")
    monkeypatch.setattr(
        primer_query,
        "GENE_TRANS_PATHS",
        {"rice": rice_trans, "soy": soy_trans, "ath": tmp_path / "missing.json", "maize": tmp_path / "missing.json"},
    )
    primer_query.clear_primer_query_cache()


def test_primer_query_uses_llm_source_and_returns_amplicon_lengths(tmp_path, monkeypatch):
    patch_primer_data(monkeypatch, tmp_path)

    llm = QpcrLLM()
    message = "帮我查 LOC_Os01g66100 的 qPCR 引物"
    classification = asyncio.run(classify_primer_query(message, llm))
    result = run_primer_query(message, classification)

    assert llm.kwargs["response_format"] == {"type": "json_object"}
    assert result["analysis"] == "primer_query"
    assert result["matches"][0]["primer_source"] == "qpcr"
    assert result["matches"][0]["canonical_id"] == "AGIS_Os01g058220"
    assert result["matches"][0]["primers"][0]["product_length"] == 195
    assert result["id_mapping_summary"][0]["source_id"] == "LOC_Os01g66100"
    assert result["id_mapping_summary"][0]["canonical_id"] == "AGIS_Os01g058220"


def test_primer_query_auto_returns_first_available_source(tmp_path, monkeypatch):
    patch_primer_data(monkeypatch, tmp_path)

    result = run_primer_query(
        "AGIS_Os01g058220 的引物",
        {"genes": ["AGIS_Os01g058220"], "species": ["rice"], "primer_sources": ["auto"], "top_k": 5},
    )

    assert result["matches"][0]["primer_source"] == "mutant"
    assert result["matches"][0]["primers"][0]["product_length"] == 703


def test_primer_query_maps_gmw82_to_glyma_for_soybean(tmp_path, monkeypatch):
    patch_primer_data(monkeypatch, tmp_path)

    result = run_primer_query(
        "GmW82.01G000100 的 qPCR 引物",
        {"genes": ["GmW82.01G000100"], "species": ["soy"], "primer_sources": ["qpcr"], "top_k": 5},
    )

    assert result["matches"][0]["canonical_id"] == "Glyma.01G000100"
    assert result["matches"][0]["query_id"] == "glyma.01g000100"
    assert result["matches"][0]["primers"][0]["product_length"] == 155


def test_primer_query_not_found_reports_design_failure_reason(tmp_path, monkeypatch):
    patch_primer_data(monkeypatch, tmp_path)

    result = run_primer_query(
        "AGIS_Os01g999999 的 qPCR 引物",
        {"genes": ["AGIS_Os01g999999"], "species": ["rice"], "primer_sources": ["qpcr"], "top_k": 5},
    )

    assert result["matches"] == []
    assert "GC含量异常" in result["not_found"][0]["reason_zh"]
    assert "无法设计正确引物" in result["not_found"][0]["reason_zh"]


def test_primer_query_registered_skill_executes(tmp_path, monkeypatch):
    patch_primer_data(monkeypatch, tmp_path)

    skill = load_skill(Path("skill/primer_query.md"))
    output = asyncio.run(execute_skill("LOC_Os01g66100 的 qPCR 引物", skill, QpcrLLM()))

    assert output["mode"] == "deterministic_query"
    assert output["result"]["analysis"] == "primer_query"
    assert output["result"]["matches"][0]["primers"][0]["product_length"] == 195

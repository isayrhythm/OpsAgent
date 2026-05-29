import asyncio
import os
from pathlib import Path

import pytest

from backend.app.config import DATA_DIR
from backend.app.services import gene_mutant_query, gene_phenotype_prediction, primer_query
from backend.app.services.deepseek_client import DeepSeekClient


def test_gene_trans_path_contract():
    shared = DATA_DIR / "gene_trans"

    assert primer_query.GENE_TRANS_PATHS == {
        "ath": shared / "ath_gene_trans.json",
        "rice": shared / "rice_gene_trans.json",
        "maize": shared / "maize_gene_trans.json",
        "soy": shared / "soy_gene_trans.json",
    }
    assert gene_phenotype_prediction.GENE_TRANS_PATHS == {
        "maize": shared / "maize_gene_trans.json",
        "rice": shared / "rice_gene_trans.json",
    }
    assert gene_mutant_query.GENE_TRANS_PATHS == {
        "ath": shared / "ath_gene_trans.json",
        "rice": DATA_DIR / "mutant_db" / "rice_gene_trans.json",
        "soy": shared / "soy_gene_trans.json",
        "maize": shared / "maize_gene_trans.json",
    }


def test_real_primer_query_reads_local_data_when_available():
    _skip_if_missing(
        [
            DATA_DIR / "gene_trans" / "rice_gene_trans.json",
            DATA_DIR / "primers" / "qpcr.parquet",
        ]
    )
    primer_query.clear_primer_query_cache()

    result = primer_query.run_primer_query(
        "design qPCR primers for LOC_Os01g66100",
        {
            "genes": ["LOC_Os01g66100"],
            "species": ["rice"],
            "primer_sources": ["qpcr"],
            "top_k": 1,
        },
    )

    assert result["analysis"] == "primer_query"
    assert result["matches"]
    match = result["matches"][0]
    assert match["canonical_id"] == "AGIS_Os01g058220"
    assert match["primer_source"] == "qpcr"
    assert match["primers"][0]["product_length"] is not None


def test_real_primer_llm_classifier_when_enabled():
    if os.getenv("OPSAGENT_REAL_LLM_TESTS") != "1":
        pytest.skip("set OPSAGENT_REAL_LLM_TESTS=1 to call the real LLM")
    llm = DeepSeekClient()
    if not llm.available:
        pytest.skip("DEEPSEEK_API_KEY is not configured")

    classification = asyncio.run(classify_with_real_llm(llm))

    assert "LOC_Os01g66100" in classification["genes"]
    assert "rice" in classification["species"]
    assert "qpcr" in classification["primer_sources"]


async def classify_with_real_llm(llm: DeepSeekClient) -> dict:
    return await primer_query.classify_primer_query("设计 LOC_Os01g66100 的 qPCR 引物", llm)


def _skip_if_missing(paths: list[Path]) -> None:
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip("local data files are not available: " + ", ".join(missing))

import json
from pathlib import Path

from backend.app.skill_tools import gene_info_lookup
from backend.app.skill_tools.gene_info_lookup import clear_gene_info_lookup_cache, enrich_blast_hits, resolve_gene_record


def patch_gene_info_data(monkeypatch, tmp_path: Path) -> None:
    trans_path = tmp_path / "soy_gene_trans.json"
    info_path = tmp_path / "soy_gene_info.json"
    trans_path.write_text(
        json.dumps({"gmw82.01g000100": "Glyma.01G000100"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    info_path.write_text(
        json.dumps(
            {
                "Glyma.01G000100": "\n".join(
                    [
                        "# soybean gene info",
                        "Uniport Entry：A0A0R0L9N0",
                        "Uniport蛋白质名称：Example kinase",
                        "Uniport蛋白功能：Regulates stress response",
                        "eggnog-mapper蛋白注释：Protein kinase-like protein",
                        "基因本体注释：GO:0000001,GO:0000002",
                        "蛋白质结构域家族：Pkinase",
                    ]
                )
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(gene_info_lookup, "GENE_TRANS_PATHS", {"soy": trans_path})
    monkeypatch.setattr(gene_info_lookup, "GENE_INFO_PATHS", {"soy": info_path})
    clear_gene_info_lookup_cache()


def test_resolve_gene_record_strips_transcript_suffix(tmp_path, monkeypatch) -> None:
    patch_gene_info_data(monkeypatch, tmp_path)

    mapping = resolve_gene_record("soy", "GmW82.01G000100.1")

    assert mapping == {
        "source_id": "GmW82.01G000100.1",
        "species": "soy",
        "canonical_id": "Glyma.01G000100",
        "matched_by": "gene_trans",
    }


def test_enrich_blast_hits_adds_compact_function_summary(tmp_path, monkeypatch) -> None:
    patch_gene_info_data(monkeypatch, tmp_path)
    hits = [{"species": "soy", "subject_id": "GmW82.01G000100.1"}]

    summary = enrich_blast_hits(hits)

    assert summary == {
        "queried_hits": 1,
        "mapped_hits": 1,
        "annotated_hits": 1,
        "summary_chars_per_hit": 700,
    }
    assert hits[0]["gene_info"]["canonical_id"] == "Glyma.01G000100"
    assert "Regulates stress response" in hits[0]["gene_info"]["function_summary"]
    assert "Protein kinase-like protein" in hits[0]["gene_info"]["function_summary"]
    assert "A0A0R0L9N0" not in hits[0]["gene_info"]["function_summary"]


def test_enrich_blast_hits_marks_unmapped_records_without_inventing_function(tmp_path, monkeypatch) -> None:
    patch_gene_info_data(monkeypatch, tmp_path)
    hits = [{"species": "soy", "subject_id": "GmW82.99G999999.1"}]

    enrich_blast_hits(hits)

    assert hits[0]["gene_info"]["matched"] is False
    assert "function_summary" not in hits[0]["gene_info"]

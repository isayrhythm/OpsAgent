import json

import pandas as pd

from backend.app.services import gene_mutant_query


def test_gene_mutant_query_resolves_rice_alias_and_queries_bgbio(tmp_path, monkeypatch):
    trans_path = tmp_path / "rice_gene_trans.json"
    data_path = tmp_path / "rice_bgbio.parquet"
    trans_path.write_text(json.dumps({"cold1": "LOC_Os04g51180"}), encoding="utf-8")
    pd.DataFrame(
        [
            {
                "种质类型": "突变体种子",
                "物种/品种": "水稻/ZH11",
                "载体骨架": "BGK03",
                "基因号": "LOC_Os04g51180",
                "靶点序列": "ACGT",
                "鉴定分析": "纯合突变",
            },
            {
                "种质类型": "突变体种子",
                "物种/品种": "水稻/ZH11",
                "载体骨架": "BGK03",
                "基因号": "LOC_Os01g00010",
                "靶点序列": "TTTT",
                "鉴定分析": "其他",
            },
        ]
    ).to_parquet(data_path, index=False)

    monkeypatch.setattr(gene_mutant_query, "GENE_TRANS_PATHS", {"rice": trans_path})
    monkeypatch.setattr(
        gene_mutant_query,
        "MUTANT_DATASETS",
        {
            "rice": gene_mutant_query.MutantDataset(
                species="rice",
                species_label="rice",
                database="BGBIO",
                path=data_path,
                gene_column="基因号",
                record_fields={
                    "germplasm_type": "种质类型",
                    "gene_id": "基因号",
                    "validation": "鉴定分析",
                },
                purchase_url_template="https://www.seedseek.cn/?locus={gene_id}",
                read_full_table=True,
            )
        },
    )

    result = gene_mutant_query.run_gene_mutant_query("COLD1 有没有突变体？")

    assert result["gene_mappings"][0]["canonical_id"] == "LOC_Os04g51180"
    assert result["id_mapping_performed"] is True
    assert result["id_mapping_summary"][0]["source_id"] == "COLD1"
    assert result["id_mapping_summary"][0]["canonical_id"] == "LOC_Os04g51180"
    assert result["matches"][0]["has_mutant"] is True
    assert result["matches"][0]["total_hits"] == 1
    assert result["matches"][0]["purchase_url"] == "https://www.seedseek.cn/?locus=LOC_Os04g51180"
    assert result["matches"][0]["records"][0]["validation"] == "纯合突变"


def test_gene_mutant_query_queries_ath_abrc_by_direct_id(tmp_path, monkeypatch):
    data_path = tmp_path / "ath_abrc.parquet"
    pd.DataFrame(
        [
            {
                "gene_id": "AT2G30410",
                "url": "586499",
                "Name / Stock Number": "SALK_103838",
                "NASC stock number": "N603838",
                "Base / Commercial Price": "$15 / $120",
                "description": "Sequence-indexed T-DNA insertion line",
            }
        ]
    ).to_parquet(data_path, index=False)

    monkeypatch.setattr(gene_mutant_query, "GENE_TRANS_PATHS", {"ath": tmp_path / "missing.json"})
    monkeypatch.setattr(
        gene_mutant_query,
        "MUTANT_DATASETS",
        {
            "ath": gene_mutant_query.MutantDataset(
                species="ath",
                species_label="Arabidopsis",
                database="ABRC/NASC",
                path=data_path,
                gene_column="gene_id",
                record_fields={
                    "gene_id": "gene_id",
                    "stock_number": "Name / Stock Number",
                    "description": "description",
                },
            )
        },
    )

    result = gene_mutant_query.run_gene_mutant_query("AT2G30410 有没有 T-DNA 突变体种子？")

    assert result["matches"][0]["database"] == "ABRC/NASC"
    assert result["matches"][0]["records"][0]["stock_number"] == "SALK_103838"


def test_gene_mutant_query_reports_species_with_mapping_but_no_database(tmp_path, monkeypatch):
    trans_path = tmp_path / "soy_gene_trans.json"
    trans_path.write_text(json.dumps({"glyma.01g000100": "Glyma.01G000100"}), encoding="utf-8")
    monkeypatch.setattr(gene_mutant_query, "GENE_TRANS_PATHS", {"soy": trans_path})
    monkeypatch.setattr(gene_mutant_query, "MUTANT_DATASETS", {})

    result = gene_mutant_query.run_gene_mutant_query("Glyma.01G000100 有没有突变体？")

    assert result["matches"] == []
    assert result["not_found"][0]["species"] == "soy"
    assert result["id_mapping_performed"] is True
    assert result["id_mapping_summary"][0]["source_id"] == "Glyma.01G000100"
    assert result["id_mapping_summary"][0]["canonical_id"] == "Glyma.01G000100"
    assert "No mutant database" in result["not_found"][0]["reason"]

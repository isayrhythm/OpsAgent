import json

from backend.app.services import gene_phenotype_prediction as predictor


def test_gene_phenotype_prediction_uses_mapping_and_top_k(tmp_path, monkeypatch):
    trans_path = tmp_path / "rice_gene_trans.json"
    data_path = tmp_path / "rice_lte_result.csv"
    trans_path.write_text(json.dumps({"loc_os07g48050": "AGIS_Os07g033940"}), encoding="utf-8")
    data_path.write_text(
        "\n".join(
            [
                "gene_id,phenotype,pred_score",
                "agis_os07g033940,grain_number_per_panicle,0.10",
                "agis_os07g033940,plant_height,0.80",
                "agis_os07g033940,drought_tolerance,0.40",
                "agis_os01g000010,other_trait,0.99",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(predictor, "PREDICTION_DATASETS", {"rice": data_path})
    monkeypatch.setattr(predictor, "PREDICTION_CSV_FALLBACKS", {"rice": data_path})
    monkeypatch.setattr(predictor, "GENE_TRANS_PATHS", {"rice": trans_path})

    result = predictor.run_gene_phenotype_prediction("预测水稻 LOC_Os07g48050 的表型，前2个")

    assert result["matches"][0]["canonical_id"] == "AGIS_Os07g033940"
    assert result["gene_mappings"] == [
        {
            "input": "LOC_Os07g48050",
            "species": "rice",
            "species_label": "水稻",
            "canonical_id": "AGIS_Os07g033940",
            "query_id": "agis_os07g033940",
            "matched_by": "gene_trans",
        }
    ]
    assert result["id_mapping_performed"] is True
    assert result["id_mapping_summary"][0]["source_id"] == "LOC_Os07g48050"
    assert result["id_mapping_summary"][0]["canonical_id"] == "AGIS_Os07g033940"
    assert "ID mapping applied: LOC_Os07g48050 -> AGIS_Os07g033940" in result["id_mapping_summary"][0]["message"]
    assert [item["phenotype"] for item in result["matches"][0]["predictions"]] == [
        "plant_height",
        "drought_tolerance",
    ]


def test_gene_phenotype_prediction_ignores_unmapped_sentence_tokens(tmp_path, monkeypatch):
    trans_path = tmp_path / "maize_gene_trans.json"
    data_path = tmp_path / "maize_lte_result.csv"
    trans_path.write_text(json.dumps({"zm00001eb123456": "Zm00001eb123456"}), encoding="utf-8")
    data_path.write_text(
        "\n".join(
            [
                "gene_id,phenotype,pred_score",
                "zm00001eb123456,leaf_angle,0.20",
                "zm00001eb123456,kernel_weight,0.90",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(predictor, "PREDICTION_DATASETS", {"maize": data_path})
    monkeypatch.setattr(predictor, "PREDICTION_CSV_FALLBACKS", {"maize": data_path})
    monkeypatch.setattr(predictor, "GENE_TRANS_PATHS", {"maize": trans_path})

    result = predictor.run_gene_phenotype_prediction("please predict Zm00001eb123456 possible phenotype")

    assert result["species_searched"] == ["maize"]
    assert result["matches"][0]["predictions"][0]["phenotype"] == "kernel_weight"
    assert result["not_found"] == []


def test_gene_phenotype_prediction_can_resolve_gene_from_recent_focus(tmp_path, monkeypatch):
    trans_path = tmp_path / "rice_gene_trans.json"
    data_path = tmp_path / "rice_lte_result.csv"
    trans_path.write_text(json.dumps({"loc_os07g48050": "AGIS_Os07g043560"}), encoding="utf-8")
    data_path.write_text(
        "\n".join(
            [
                "gene_id,phenotype,pred_score",
                "agis_os07g043560,rice_blast_resistance,0.90",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(predictor, "PREDICTION_DATASETS", {"rice": data_path})
    monkeypatch.setattr(predictor, "PREDICTION_CSV_FALLBACKS", {"rice": data_path})
    monkeypatch.setattr(predictor, "GENE_TRANS_PATHS", {"rice": trans_path})

    result = predictor.run_gene_phenotype_prediction(
        "当前用户请求：继续查啊\n"
        "上一轮用户请求：LOC_Os07g48050 可能跟哪些性状相关？\n"
        "上一轮助手回复：执行器未注册，无法完成预测。"
    )

    assert result["matches"][0]["canonical_id"] == "AGIS_Os07g043560"
    assert result["matches"][0]["predictions"][0]["phenotype"] == "rice_blast_resistance"

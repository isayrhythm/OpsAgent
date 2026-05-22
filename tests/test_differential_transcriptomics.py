from backend.app.services.differential_transcriptomics import (
    _analysis_parameters,
    _choose_comparisons,
    _cluster_heatmap_rows,
    _report_html,
)


def test_transcriptomics_auto_pairs_treatment_groups() -> None:
    comparisons = _choose_comparisons(
        {
            "MT-D": ["MT-D1", "MT-D2"],
            "WT-D": ["WT-D1", "WT-D2", "WT-D3"],
            "MT-C": ["MT-C1", "MT-C2"],
            "WT-C": ["WT-C1", "WT-C2", "WT-C3"],
        },
        {},
    )

    assert [item["comparison"] for item in comparisons] == ["MT-C vs WT-C", "MT-D vs WT-D"]


def test_transcriptomics_parameters_accept_padj_and_log2fc_thresholds() -> None:
    parameters = _analysis_parameters({"padj_cutoff": 0.01, "log2_fc_cutoff": 1.5})

    assert parameters == {"padj_cutoff": 0.01, "log2_fc_cutoff": 1.5}


def test_transcriptomics_heatmap_clusters_similar_gene_rows_together() -> None:
    rows = [
        {"id": "isolated", "values": [3.0, -3.0]},
        {"id": "gene_a", "values": [0.0, 0.1]},
        {"id": "gene_b", "values": [0.1, 0.0]},
    ]

    ids = [row["id"] for row in _cluster_heatmap_rows(rows)]

    assert abs(ids.index("gene_a") - ids.index("gene_b")) == 1


def test_transcriptomics_report_uses_local_plotly() -> None:
    summaries = [
        {
            "slug": "MT-DvsWT-D",
            "comparison": "MT-D vs WT-D",
            "numerator": "MT-D",
            "denominator": "WT-D",
            "total": 10,
            "significant": 3,
            "up": 2,
            "down": 1,
            "files": {
                "all_genes": "run/MT-DvsWT-D_all_genes.csv",
                "significant_genes": "run/MT-DvsWT-D_significant_genes.csv",
            },
        }
    ]
    payload = {
        "MT-DvsWT-D": {
            "summary": {"comparison": "MT-D vs WT-D"},
            "volcano": [{"id": "AGIS_Os01g000010", "x": 2, "y": 4, "regulation": "up", "padj": 0.0001}],
            "heatmap": {"samples": ["MT-D1", "WT-D1"], "rows": [{"id": "AGIS_Os01g000010", "values": [1, -1]}]},
        }
    }

    report = _report_html(payload, summaries, {"padj_cutoff": 0.05, "log2_fc_cutoff": 1.0})

    assert '<script src="plotly.min.js"></script>' in report
    assert "displayModeBar:false" in report
    assert "DESeq2 thresholds" in report

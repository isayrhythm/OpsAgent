import subprocess
from pathlib import Path

import pandas as pd

from backend.app.schemas import UploadedFileSummary
from backend.app.services.differential_transcriptomics import (
    _analysis_parameters,
    _choose_comparisons,
    _cluster_heatmap_rows,
    _report_html,
    _sanitize_counts_matrix_for_deseq2,
    run_differential_transcriptomics_analysis,
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
    assert 'title:{text:"log2 fold change", standoff:16}' in report
    assert 'title:{text:"-log10 adjusted p-value", standoff:16}' in report


def test_sanitize_counts_matrix_for_deseq2_removes_invalid_rows(tmp_path: Path) -> None:
    matrix = tmp_path / "standard_matrix.csv"
    metadata = tmp_path / "sample_metadata.csv"
    output = tmp_path / "retry_standard_matrix.csv"
    matrix.write_text(
        "\n".join(
            [
                "feature_id,MT1,MT2,WT1,WT2",
                "gene_good,10,12,5,6",
                "gene_bad,NA,11,7,8",
                "gene_negative,4,-1,5,6",
                "gene_zero,0,0,0,0",
            ]
        ),
        encoding="utf-8",
    )
    metadata.write_text("sample,condition\nMT1,MT\nMT2,MT\nWT1,WT\nWT2,WT\n", encoding="utf-8")

    repair = _sanitize_counts_matrix_for_deseq2(matrix, metadata, output)
    cleaned = pd.read_csv(output)

    assert repair["retryable"] is True
    assert repair["removed_rows"] == 3
    assert repair["remaining_rows"] == 1
    assert cleaned["feature_id"].tolist() == ["gene_good"]


def test_transcriptomics_analysis_retries_with_repaired_counts_matrix(tmp_path: Path, monkeypatch) -> None:
    matrix = tmp_path / "standard_matrix.csv"
    metadata = tmp_path / "sample_metadata.csv"
    matrix.write_text(
        "\n".join(
            [
                "feature_id,MT1,MT2,WT1,WT2",
                "gene_good,10,12,5,6",
                "gene_bad,bad,11,7,8",
            ]
        ),
        encoding="utf-8",
    )
    metadata.write_text("sample,condition\nMT1,MT\nMT2,MT\nWT1,WT\nWT2,WT\n", encoding="utf-8")
    attachment = UploadedFileSummary(
        file_id="rna",
        filename="rna.csv",
        size=1,
        path=str(matrix),
        intake={
            "status": "ready",
            "data_family": "transcriptomics",
            "data_type": "expression_matrix",
            "feature_count": 2,
            "sample_count": 4,
            "sample_groups": {"MT": ["MT1", "MT2"], "WT": ["WT1", "WT2"]},
            "standard_files": {"matrix": str(matrix), "sample_metadata": str(metadata)},
        },
    )
    calls = {"count": 0}

    def fake_run(command, **_kwargs):
        calls["count"] += 1
        output_dir = Path(command[5])
        if calls["count"] == 1:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="NA values are not allowed in count matrix")
        comparisons = pd.read_csv(command[4])
        slug = comparisons["slug"].iloc[0]
        pd.DataFrame({"gene_id": ["gene_good"], "MT1": [11], "MT2": [12], "WT1": [5], "WT2": [6]}).to_csv(
            output_dir / "normalized_counts.csv",
            index=False,
        )
        pd.DataFrame(
            {
                "gene_id": ["gene_good"],
                "log2FoldChange": [1.2],
                "padj": [0.01],
                "pvalue": [0.005],
                "regulation": ["up"],
            }
        ).to_csv(output_dir / f"{slug}_all_genes.csv", index=False)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr("backend.app.services.differential_transcriptomics._find_rscript", lambda: Path("Rscript"))
    monkeypatch.setattr("backend.app.services.differential_transcriptomics.subprocess.run", fake_run)

    result = run_differential_transcriptomics_analysis(
        [attachment],
        {"comparisons": [{"numerator": "MT", "denominator": "WT"}]},
    )

    assert result["status"] == "completed"
    assert calls["count"] == 2
    assert result["retry"]["attempted"] is True
    assert result["retry"]["second_returncode"] == 0
    assert result["feature_count"] == 1
    assert "repaired_standard_matrix" in result["files"]

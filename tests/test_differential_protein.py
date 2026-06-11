from backend.app.skill_tools.differential_protein import (
    _analysis_parameters,
    _choose_comparisons,
    _cluster_heatmap_rows,
    _report_html,
)


def test_cluster_heatmap_rows_keeps_similar_expression_patterns_adjacent() -> None:
    rows = [
        {"id": "up-a", "name": "up-a", "values": [2.0, 1.9, -1.9, -2.0]},
        {"id": "down-a", "name": "down-a", "values": [-2.0, -1.8, 1.8, 2.0]},
        {"id": "up-b", "name": "up-b", "values": [1.8, 2.0, -2.0, -1.7]},
        {"id": "down-b", "name": "down-b", "values": [-1.9, -2.0, 2.0, 1.7]},
    ]

    ordered_ids = [row["id"] for row in _cluster_heatmap_rows(rows)]

    assert abs(ordered_ids.index("up-a") - ordered_ids.index("up-b")) == 1
    assert abs(ordered_ids.index("down-a") - ordered_ids.index("down-b")) == 1


def test_report_uses_local_plotly_for_interactive_plots() -> None:
    report = _report_html(
        {
            "MTvsWT": {
                "summary": {
                    "slug": "MTvsWT",
                    "comparison": "MT vs WT",
                    "total": 4,
                    "differential": 2,
                    "up": 1,
                    "down": 1,
                },
                "volcano": [
                    {"id": "P1", "name": "P1", "x": 1.2, "y": 3.0, "fold_change": 2.3, "pvalue": 0.001, "regulation": "up"}
                ],
                "heatmap": {"samples": ["WT1", "MT1"], "rows": [{"id": "P1", "name": "P1", "values": [-1, 1]}]},
                "rows": [{"feature_id": "P1", "feature_name": "P1", "fold_change": "2.3", "log2_fc": "1.2", "pvalue": "0.001", "padj": "0.01", "regulation": "up"}],
            }
        },
        [
            {
                "slug": "MTvsWT",
                "comparison": "MT vs WT",
                "total": 4,
                "differential": 2,
                "up": 1,
                "down": 1,
                "files": {
                    "all_results": "run/MTvsWT_all_results.csv",
                    "differential_results": "run/MTvsWT_differential_results.csv",
                },
            }
        ],
        {"pvalue_cutoff": 0.01, "fold_change_cutoff": 2.0},
    )

    assert '<script src="plotly.min.js"></script>' in report
    assert 'type:"scattergl"' in report
    assert 'type:"heatmap"' in report
    assert "displayModeBar:false" in report
    assert "Clustered protein axis" in report
    assert "p-value &lt; 0.01" in report
    assert '<select id="comparison">' in report
    assert 'title:{ text:"log2 fold change", standoff:16 }' in report
    assert 'title:{ text:"-log10 p-value", standoff:16 }' in report


def test_analysis_parameters_accept_explicit_thresholds() -> None:
    parameters = _analysis_parameters({"pvalue_cutoff": 0.01, "fold_change_cutoff": 2})

    assert parameters == {"pvalue_cutoff": 0.01, "fold_change_cutoff": 2.0}


def test_protein_choose_comparisons_accepts_multiple_requested_pairs() -> None:
    comparisons = _choose_comparisons(
        {"WT": ["WT1", "WT2"], "MT1": ["MT11", "MT12"], "MT2": ["MT21", "MT22"]},
        {
            "comparisons": [
                {"numerator": "MT1", "denominator": "WT"},
                {"numerator": "MT2", "denominator": "WT"},
            ]
        },
    )

    assert [item["comparison"] for item in comparisons] == ["MT1 vs WT", "MT2 vs WT"]


def test_protein_choose_comparisons_auto_pairs_named_multi_group_matrix() -> None:
    comparisons = _choose_comparisons(
        {
            "MT-D": ["MT-D1", "MT-D2"],
            "WT-D": ["WT-D1", "WT-D2"],
            "MT-C": ["MT-C1", "MT-C2"],
            "WT-C": ["WT-C1", "WT-C2"],
        },
        {},
    )

    assert [item["comparison"] for item in comparisons] == ["MT-C vs WT-C", "MT-D vs WT-D"]

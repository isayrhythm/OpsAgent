from backend.app.services.differential_protein import _cluster_heatmap_rows, _report_html


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
            "group_a": "WT",
            "group_b": "MT",
        },
        {"total": 4, "differential": 2, "up": 1, "down": 1},
        [{"id": "P1", "name": "P1", "x": 1.2, "y": 3.0, "fold_change": 2.3, "pvalue": 0.001, "regulation": "up"}],
        {"samples": ["WT1", "MT1"], "rows": [{"id": "P1", "name": "P1", "values": [-1, 1]}]},
        "<tr><td>P1</td></tr>",
    )

    assert '<script src="plotly.min.js"></script>' in report
    assert 'type:"scattergl"' in report
    assert 'type:"heatmap"' in report
    assert "displayModeBar:false" in report
    assert "Clustered protein axis" in report

import csv

from backend.app.services import gene_function_research_path
from backend.app.services.gene_function_research_path import parse_research_path_steps, run_gene_function_research_path_query


MARKDOWN = """
Path overview.

## Route of Gene Function Exploration

| Steps | Stage Operation | Figures | Gene Function Exploration |
| --- | --- | --- | --- |
| 1 | Rescue phenotype | Fig. 2 | - Hypothesis: Rescue can reveal function.<br><br>- Methods: 1. Feed seedlings<br>2. Measure hypocotyls<br><br>- Results: 1. Mutant phenotype was rescued.<br><br>- Step_Conclusion: The gene acts after the supplied metabolite. |
| 2 | Molecular assay | Fig. 6 | - Hypothesis: Protein state should change.<br><br>- Methods: 1. Immunoblotting<br><br>- Results: 1. Protein degradation was restored.<br><br>- Step_Conclusion: Molecular evidence supports the path. |

## Conclusion

Conclusion text.
"""


def test_parse_research_path_steps_extracts_route_table() -> None:
    steps = parse_research_path_steps(MARKDOWN)

    assert [step["step"] for step in steps] == ["1", "2"]
    assert steps[0]["stage_operation"] == "Rescue phenotype"
    assert steps[0]["figures"] == "Fig. 2"
    assert "Feed seedlings" in steps[0]["methods"]
    assert steps[1]["step_conclusion"] == "Molecular evidence supports the path."


def test_research_path_query_indexes_target_gene_and_returns_ui_block(tmp_path, monkeypatch) -> None:
    dataset = tmp_path / "paths.csv"
    with dataset.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["paper_id", "title", "title_targetGene_id", "targetGene", "final_md_content", "origin_textblock"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "paper_id": "Atha_0",
                "title": "HY2 evidence paper",
                "title_targetGene_id": "HY2 evidence paper__HY2",
                "targetGene": "HY2",
                "final_md_content": MARKDOWN,
                "origin_textblock": "",
            }
        )
    monkeypatch.setattr(gene_function_research_path, "RESEARCH_PATH_DATASET", dataset)

    result = run_gene_function_research_path_query("HY2 的功能研究路径")

    assert result["status"] == "completed"
    assert result["genes"] == ["HY2"]
    assert result["matches"][0]["title"] == "HY2 evidence paper"
    assert result["matches"][0]["gene_id"] == "HY2"
    assert result["ui_blocks"][0]["steps"][0]["stage_operation"] == "Rescue phenotype"

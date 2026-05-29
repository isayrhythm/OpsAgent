import asyncio
import json
from pathlib import Path

from backend.app.services import trait2gene
from backend.app.services.code_executor import execute_skill
from backend.app.services.skill_loader import load_skill
from backend.app.services.trait2gene import TraitDataset


class FakeSettings:
    router_model = "fake-router"


class FakeLLM:
    available = True
    settings = FakeSettings()

    async def chat(self, messages, **kwargs):
        self.messages = messages
        self.kwargs = kwargs
        return json.dumps(
            {
                "selected": [{"species": "rice", "categories": ["plant height"]}],
                "top_k": 2,
                "reason": "matched user trait",
            }
        )


def write_trait_csv(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "Target_geneID,trait,Target_gene_name,Literature_name,classify2,source",
                "G1,tall plant,PH1,Paper A,plant height,literature",
                "G1,plant stature,PH1,Paper B,plant height,RAP-DB",
                "G2,height phenotype,PH2,Paper C,plant height,RAP-DB",
                "G1,cold assay,PH1,Paper D,cold tolerance,literature",
                "G3,cold only,CT1,Paper E,cold tolerance,literature",
            ]
        ),
        encoding="utf-8",
    )


def patch_dataset(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(
        trait2gene,
        "TRAIT_DATASETS",
        {"rice": TraitDataset(species="rice", species_label="rice", path=path)},
    )
    trait2gene.clear_trait2gene_cache()


def test_trait2gene_uses_llm_classification_and_ranks_genes(tmp_path, monkeypatch):
    data_path = tmp_path / "rice_trait2gene.csv"
    write_trait_csv(data_path)
    patch_dataset(monkeypatch, data_path)

    llm = FakeLLM()
    classification = asyncio.run(trait2gene.classify_trait2gene_query("rice plant height genes", llm))
    result = trait2gene.run_trait2gene_query("rice plant height genes", classification)

    assert llm.kwargs["response_format"] == {"type": "json_object"}
    assert result["analysis"] == "trait2gene_query"
    assert result["matches"][0]["categories"] == ["plant height"]
    assert result["matches"][0]["total_genes"] == 2
    assert result["matches"][0]["genes"][0]["gene_id"] == "G1"
    assert result["matches"][0]["genes"][0]["evidence_count"] == 2
    assert result["matches"][0]["genes"][1]["gene_id"] == "G2"


def test_trait2gene_multiple_categories_use_gene_intersection(tmp_path, monkeypatch):
    data_path = tmp_path / "rice_trait2gene.csv"
    write_trait_csv(data_path)
    patch_dataset(monkeypatch, data_path)

    result = trait2gene.run_trait2gene_query(
        "genes for plant height and cold tolerance",
        {
            "selected": [{"species": "rice", "categories": ["plant height", "cold tolerance"]}],
            "top_k": 20,
            "reason": "two traits",
        },
    )

    assert result["matches"][0]["total_genes"] == 1
    assert [item["gene_id"] for item in result["matches"][0]["genes"]] == ["G1"]


def test_trait2gene_registered_skill_executes(tmp_path, monkeypatch):
    data_path = tmp_path / "rice_trait2gene.csv"
    write_trait_csv(data_path)
    patch_dataset(monkeypatch, data_path)

    skill = load_skill(Path("skill/trait2gene_query.md"))
    output = asyncio.run(execute_skill("rice plant height genes", skill, FakeLLM()))

    assert output["mode"] == "deterministic_query"
    assert output["result"]["analysis"] == "trait2gene_query"
    assert output["result"]["matches"][0]["genes"][0]["gene_id"] == "G1"


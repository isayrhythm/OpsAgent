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


class RepairLLM:
    available = True
    settings = FakeSettings()

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return json.dumps(
                {
                    "selected": [{"species": "rice", "categories": ["grain yield"]}],
                    "top_k": 20,
                    "reason": "broad yield",
                }
            )
        return json.dumps(
            {
                "selected": [
                    {
                        "species": "rice",
                        "categories": ["grain number per panicle", "thousand-grain weight"],
                        "match_mode": "any",
                    }
                ],
                "top_k": 20,
                "reason": "repair to valid yield-component categories",
            }
        )


class SoyYieldLLM:
    available = True
    settings = FakeSettings()

    async def chat(self, messages, **kwargs):
        return json.dumps(
            {
                "selected": [
                    {
                        "species": "soy",
                        "categories": ["100-seed weight", "number of pods per plant", "number of seed per pod"],
                    }
                ],
                "top_k": 20,
                "reason": "soybean yield components",
            }
        )


class CompositeTraitLLM:
    available = True
    settings = FakeSettings()

    async def chat(self, messages, **kwargs):
        return json.dumps(
            {
                "selected": [
                    {
                        "species": "soy",
                        "categories": ["100-seed weight", "number of pods per plant"],
                    },
                    {
                        "species": "rice",
                        "categories": ["plant height"],
                        "match_mode": "all",
                    },
                ],
                "top_k": 20,
                "reason": "soybean yield and rice plant height",
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


def write_yield_csv(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "Target_geneID,trait,Target_gene_name,Literature_name,classify2,source",
                "G1,grain number trait,GN1,Paper A,grain number per panicle,literature",
                "G2,grain weight trait,GW1,Paper B,thousand-grain weight,RAP-DB",
                "G3,plant height trait,PH1,Paper C,plant height,RAP-DB",
            ]
        ),
        encoding="utf-8",
    )


def write_soy_yield_csv(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "Target_geneID,trait,Target_gene_name,Literature_name,classify2,source",
                "S1,seed weight trait,SW1,Paper A,100-seed weight,pubmed",
                "S2,pod number trait,PN1,Paper B,number of pods per plant,pubmed",
                "S3,seed per pod trait,SP1,Paper C,number of seed per pod,pubmed",
                "S4,flower color trait,FC1,Paper D,flower color,pubmed",
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


def patch_soy_dataset(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(
        trait2gene,
        "TRAIT_DATASETS",
        {"soy": TraitDataset(species="soy", species_label="soybean", path=path)},
    )
    trait2gene.clear_trait2gene_cache()


def patch_soy_and_rice_datasets(monkeypatch, soy_path: Path, rice_path: Path) -> None:
    monkeypatch.setattr(
        trait2gene,
        "TRAIT_DATASETS",
        {
            "soy": TraitDataset(species="soy", species_label="soybean", path=soy_path),
            "rice": TraitDataset(species="rice", species_label="rice", path=rice_path),
        },
    )
    trait2gene.clear_trait2gene_cache()


def test_trait2gene_needs_species_when_message_has_no_species():
    classification = asyncio.run(trait2gene.classify_trait2gene_query("yield related genes", FakeLLM()))
    result = trait2gene.run_trait2gene_query("yield related genes", classification)

    assert classification["needs_species"] is True
    assert result["status"] == "need_user_input"
    assert result["matches"] == []
    assert result["supported_species"]


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
    assert result["matches"][0]["genes"][0]["evidence"][0] == {
        "category": "plant height",
        "trait": "tall plant",
        "literature": "Paper A",
        "source": "literature",
    }
    assert "Paper A" in result["matches"][0]["references"]
    assert result["matches"][0]["genes"][0]["references"] == ["Paper A", "Paper B"]
    assert result["matches"][0]["genes"][1]["gene_id"] == "G2"


def test_trait2gene_multiple_categories_use_gene_intersection_only_when_explicit(tmp_path, monkeypatch):
    data_path = tmp_path / "rice_trait2gene.csv"
    write_trait_csv(data_path)
    patch_dataset(monkeypatch, data_path)

    result = trait2gene.run_trait2gene_query(
        "genes simultaneously for plant height and cold tolerance in rice",
        {
            "selected": [{"species": "rice", "categories": ["plant height", "cold tolerance"]}],
            "top_k": 20,
            "reason": "two traits",
        },
    )

    assert result["matches"][0]["total_genes"] == 1
    assert [item["gene_id"] for item in result["matches"][0]["genes"]] == ["G1"]


def test_trait2gene_broad_multi_category_trait_uses_union_by_default(tmp_path, monkeypatch):
    data_path = tmp_path / "soy_trait2gene.csv"
    write_soy_yield_csv(data_path)
    patch_soy_dataset(monkeypatch, data_path)

    message = "soybean yield related genes"
    classification = asyncio.run(trait2gene.classify_trait2gene_query(message, SoyYieldLLM()))
    result = trait2gene.run_trait2gene_query(message, classification)

    assert classification["selected"][0]["match_mode"] == "any"
    assert result["matches"][0]["match_mode"] == "any"
    assert result["matches"][0]["total_genes"] == 3
    assert {item["gene_id"] for item in result["matches"][0]["genes"]} == {"S1", "S2", "S3"}


def test_trait2gene_composite_question_returns_literature_markdown(tmp_path, monkeypatch):
    soy_path = tmp_path / "soy_trait2gene.csv"
    rice_path = tmp_path / "rice_trait2gene.csv"
    write_soy_yield_csv(soy_path)
    write_trait_csv(rice_path)
    patch_soy_and_rice_datasets(monkeypatch, soy_path, rice_path)

    message = "大豆产量相关的基因有哪些？水稻株高相关的基因有哪些？"
    classification = asyncio.run(trait2gene.classify_trait2gene_query(message, CompositeTraitLLM()))
    result = trait2gene.run_trait2gene_query(message, classification)

    assert [(match["species"], match["match_mode"]) for match in result["matches"]] == [
        ("soy", "any"),
        ("rice", "all"),
    ]
    assert result["matches"][0]["total_genes"] == 2
    assert result["matches"][1]["total_genes"] == 2
    assert result["matches"][0]["references"] == ["Paper A", "Paper B"]
    assert result["matches"][1]["references"] == ["Paper A", "Paper B", "Paper C"]
    assert result["matches"][0]["genes"][0]["evidence"][0]["literature"] == "Paper A"
    assert result["matches"][1]["genes"][0]["evidence"][0]["literature"] == "Paper A"


def test_trait2gene_repairs_invalid_llm_category_to_valid_components(tmp_path, monkeypatch):
    data_path = tmp_path / "rice_trait2gene.csv"
    write_yield_csv(data_path)
    patch_dataset(monkeypatch, data_path)

    llm = RepairLLM()
    classification = asyncio.run(trait2gene.classify_trait2gene_query("rice yield related genes", llm))
    result = trait2gene.run_trait2gene_query("rice yield related genes", classification)

    assert llm.calls == 2
    assert classification["selected"] == [
        {
            "species": "rice",
            "categories": ["grain number per panicle", "thousand-grain weight"],
            "match_mode": "any",
        }
    ]
    assert result["matches"][0]["match_mode"] == "any"
    assert result["matches"][0]["total_genes"] == 2
    assert {item["gene_id"] for item in result["matches"][0]["genes"]} == {"G1", "G2"}


def test_trait2gene_registered_skill_executes(tmp_path, monkeypatch):
    data_path = tmp_path / "rice_trait2gene.csv"
    write_trait_csv(data_path)
    patch_dataset(monkeypatch, data_path)

    skill = load_skill(Path("skill/trait2gene_query.md"))
    output = asyncio.run(execute_skill("rice plant height genes", skill, FakeLLM()))

    assert output["mode"] == "deterministic_query"
    assert output["result"]["analysis"] == "trait2gene_query"
    assert output["result"]["matches"][0]["genes"][0]["gene_id"] == "G1"

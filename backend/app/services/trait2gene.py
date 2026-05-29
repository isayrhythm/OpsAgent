from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from backend.app.config import DATA_DIR
from backend.app.services.deepseek_client import DeepSeekClient


@dataclass(frozen=True)
class TraitDataset:
    species: str
    species_label: str
    path: Path


TRAIT_DATASETS = {
    "ath": TraitDataset(
        species="ath",
        species_label="Arabidopsis",
        path=DATA_DIR / "trait2gene" / "ath_trait2gene_paper_tair_3.csv",
    ),
    "rice": TraitDataset(
        species="rice",
        species_label="rice",
        path=DATA_DIR / "trait2gene" / "genedb_172traitCategroy_v3.csv",
    ),
    "maize": TraitDataset(
        species="maize",
        species_label="maize",
        path=DATA_DIR / "trait2gene" / "maize_db_trait158_v5.csv",
    ),
    "soy": TraitDataset(
        species="soy",
        species_label="soybean",
        path=DATA_DIR / "trait2gene" / "SoyGeneDB_trait164_v2.csv",
    ),
}

COLUMN_MAP = {
    "Target_geneID": "gene_id",
    "Target_gene_name": "gene_name",
    "Literature_name": "literature",
    "classify2": "category",
    "source": "source",
    "trait": "trait",
    "information": "detail",
    "abstract": "detail",
    "Abstract": "detail",
}

DEFAULT_TOP_K = 20
MAX_TOP_K = 100
CLASSIFIER_MAX_TOKENS = 900
MAX_TRAIT_EXAMPLES = 3
MAX_REFERENCES = 10


def available_trait_categories(species_scope: list[str] | None = None) -> dict[str, list[str]]:
    species_names = species_scope or list(TRAIT_DATASETS)
    return {
        species: _load_categories(species)
        for species in species_names
        if species in TRAIT_DATASETS and TRAIT_DATASETS[species].path.is_file()
    }


async def classify_trait2gene_query(message: str, llm: DeepSeekClient) -> dict[str, Any]:
    categories = available_trait_categories()
    if not categories:
        return {
            "selected": [],
            "top_k": DEFAULT_TOP_K,
            "reason": "No trait2gene datasets are available.",
        }

    if not getattr(llm, "available", False):
        return _fallback_classification(message, categories)

    response = await llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "你是 trait2gene 工具的性状分类器。"
                    "请根据当前用户问题，从给定 species 的 available_categories 中选择最匹配的 classify2 分类，"
                    "用于查询某个性状相关的基因。"
                    "只能选择候选列表里真实存在的分类，不要创造新分类。"
                    "如果用户没有限定物种，可以选择多个物种中语义匹配的分类。"
                    "如果用户询问多个性状，categories 可以包含多个分类，后续查询会取同时关联这些分类的基因交集。"
                    "只输出 JSON。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "current_message": message,
                        "available_species": [
                            {"species": item.species, "species_label": item.species_label}
                            for item in TRAIT_DATASETS.values()
                        ],
                        "available_categories": categories,
                        "output_json_schema": {
                            "selected": [
                                {
                                    "species": "rice",
                                    "categories": ["soil salinity tolerance"],
                                }
                            ],
                            "top_k": 20,
                            "reason": "short reason",
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        model=getattr(getattr(llm, "settings", None), "router_model", None),
        temperature=0,
        max_tokens=CLASSIFIER_MAX_TOKENS,
        response_format={"type": "json_object"},
    )
    return _coerce_classification(_json_from_text(response), categories)


def run_trait2gene_query(message: str, classification: dict[str, Any]) -> dict[str, Any]:
    normalized = _coerce_classification(classification, available_trait_categories())
    top_k = _clamp_top_k(normalized.get("top_k"))
    matches = []
    not_found = []

    for selection in normalized.get("selected", []):
        species = selection.get("species")
        dataset = TRAIT_DATASETS.get(str(species))
        if dataset is None:
            not_found.append({"species": species, "reason": "Unsupported species."})
            continue

        categories = [str(item) for item in selection.get("categories", []) if str(item).strip()]
        available = set(_load_categories(dataset.species))
        matched_categories = [item for item in categories if item in available]
        missing_categories = [item for item in categories if item not in available]
        if not matched_categories:
            not_found.append(
                {
                    "species": dataset.species,
                    "species_label": dataset.species_label,
                    "requested_categories": categories,
                    "reason": "No selected trait category exists in this species dataset.",
                }
            )
            continue

        result = _query_species(dataset, matched_categories, top_k)
        if result["total_genes"] == 0:
            not_found.append(
                {
                    "species": dataset.species,
                    "species_label": dataset.species_label,
                    "requested_categories": matched_categories,
                    "reason": "No gene was found for the selected trait category combination.",
                }
            )
            continue
        if missing_categories:
            result["missing_categories"] = missing_categories
        matches.append(result)

    return {
        "status": "completed",
        "analysis": "trait2gene_query",
        "query": message,
        "classification": normalized,
        "top_k": top_k,
        "species_searched": [item["species"] for item in normalized.get("selected", [])],
        "matches": matches,
        "not_found": not_found,
    }


def clear_trait2gene_cache() -> None:
    _load_dataset.cache_clear()
    _load_categories.cache_clear()


def _query_species(dataset: TraitDataset, categories: list[str], top_k: int) -> dict[str, Any]:
    df = _load_dataset(dataset.species)
    selected = df[df["category"].isin(categories)].copy()
    if selected.empty:
        gene_set: set[str] = set()
    else:
        gene_sets = [
            set(selected.loc[selected["category"] == category, "gene_id"].dropna().astype(str))
            for category in categories
        ]
        gene_set = set.intersection(*gene_sets) if gene_sets else set()

    evidence = selected[selected["gene_id"].isin(gene_set)].copy()
    if evidence.empty:
        return {
            "species": dataset.species,
            "species_label": dataset.species_label,
            "categories": categories,
            "total_genes": 0,
            "returned_genes": 0,
            "genes": [],
            "source_counts": [],
            "references": [],
            "source_file": str(dataset.path),
        }

    gene_order = evidence["gene_id"].value_counts().index[:top_k].tolist()
    genes = [_gene_record(evidence[evidence["gene_id"] == gene_id], gene_id) for gene_id in gene_order]
    source_counts = [
        {"source": str(source), "unique_genes": int(group["gene_id"].nunique())}
        for source, group in evidence.groupby("source", dropna=True)
        if str(source).strip()
    ]
    source_counts.sort(key=lambda item: item["unique_genes"], reverse=True)

    references = [
        str(item)
        for item in evidence["literature"].dropna().astype(str).value_counts().index[:MAX_REFERENCES]
        if str(item).strip()
    ]

    return {
        "species": dataset.species,
        "species_label": dataset.species_label,
        "categories": categories,
        "total_genes": int(len(gene_set)),
        "returned_genes": len(genes),
        "genes": genes,
        "source_counts": source_counts,
        "references": references,
        "source_file": str(dataset.path),
    }


def _gene_record(rows: pd.DataFrame, gene_id: str) -> dict[str, Any]:
    gene_names = _unique_nonempty(rows.get("gene_name", pd.Series(dtype=str)), limit=8)
    categories = _unique_nonempty(rows.get("category", pd.Series(dtype=str)), limit=20)
    sources = _unique_nonempty(rows.get("source", pd.Series(dtype=str)), limit=10)
    references = _unique_nonempty(rows.get("literature", pd.Series(dtype=str)), limit=5)
    traits = [_shorten_text(item, 260) for item in _unique_nonempty(rows.get("trait", pd.Series(dtype=str)), limit=MAX_TRAIT_EXAMPLES)]
    return {
        "gene_id": gene_id,
        "gene_names": gene_names,
        "categories": categories,
        "evidence_count": int(len(rows)),
        "sources": sources,
        "references": references,
        "trait_examples": traits,
    }


@lru_cache(maxsize=None)
def _load_dataset(species: str) -> pd.DataFrame:
    dataset = TRAIT_DATASETS[species]
    header = pd.read_csv(dataset.path, nrows=0)
    usecols = [column for column in header.columns if column in COLUMN_MAP]
    df = pd.read_csv(dataset.path, usecols=usecols)
    df = df.rename(columns={column: COLUMN_MAP[column] for column in usecols})
    df = df.loc[:, ~df.columns.duplicated()]
    for column in ("gene_id", "gene_name", "literature", "category", "source", "trait", "detail"):
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].fillna("").astype(str).str.strip()
    df = df[(df["gene_id"] != "") & (df["category"] != "")]
    return df


@lru_cache(maxsize=None)
def _load_categories(species: str) -> list[str]:
    df = _load_dataset(species)
    return sorted(item for item in df["category"].dropna().astype(str).unique().tolist() if item.strip())


def _coerce_classification(payload: dict[str, Any], categories: dict[str, list[str]]) -> dict[str, Any]:
    selected = payload.get("selected")
    if not isinstance(selected, list):
        species_items = payload.get("species") or payload.get("species_searched") or []
        if isinstance(species_items, str):
            species_items = [species_items]
        category_items = payload.get("categories") or payload.get("trait_categories") or payload.get("classify2") or []
        if isinstance(category_items, str):
            category_items = [category_items]
        selected = [{"species": species, "categories": category_items} for species in species_items]

    clean_selected = []
    for item in selected:
        if not isinstance(item, dict):
            continue
        species = str(item.get("species", "")).strip().lower()
        if species not in categories:
            continue
        category_items = item.get("categories") or item.get("trait_categories") or item.get("classify2") or []
        if isinstance(category_items, str):
            category_items = [category_items]
        valid_categories = []
        seen = set()
        for category in category_items:
            category_text = str(category).strip()
            if not category_text or category_text in seen:
                continue
            seen.add(category_text)
            valid_categories.append(category_text)
        if valid_categories:
            clean_selected.append({"species": species, "categories": valid_categories})

    return {
        "selected": clean_selected,
        "top_k": _clamp_top_k(payload.get("top_k")),
        "reason": str(payload.get("reason") or "").strip(),
    }


def _fallback_classification(message: str, categories: dict[str, list[str]]) -> dict[str, Any]:
    normalized_message = _normalize_text(message)
    selected = []
    for species, category_items in categories.items():
        matches = [
            category
            for category in category_items
            if _normalize_text(category) and _normalize_text(category) in normalized_message
        ]
        if matches:
            selected.append({"species": species, "categories": matches[:3]})
    return {
        "selected": selected,
        "top_k": DEFAULT_TOP_K,
        "reason": "Matched literal classify2 text without LLM.",
    }


def _json_from_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    match = re.search(r"\{.*\}", stripped, re.S)
    if match:
        stripped = match.group(0)
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError("trait2gene classifier response must be a JSON object")
    return payload


def _clamp_top_k(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = DEFAULT_TOP_K
    return max(1, min(MAX_TOP_K, number))


def _unique_nonempty(values: pd.Series, *, limit: int) -> list[str]:
    result = []
    seen = set()
    for value in values.dropna().astype(str):
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _shorten_text(value: str, max_length: int) -> str:
    text = " ".join(str(value).split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", str(value).lower()).strip()


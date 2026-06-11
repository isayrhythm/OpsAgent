from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from backend.app.config import DATA_DIR
from backend.app.llm.calls import chat_json
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
MAX_GENE_EVIDENCE = 6
SUPPORTED_SPECIES = [
    {"species": "ath", "species_label": "Arabidopsis"},
    {"species": "rice", "species_label": "rice"},
    {"species": "maize", "species_label": "maize"},
    {"species": "soy", "species_label": "soybean"},
]


def available_trait_categories(species_scope: list[str] | None = None) -> dict[str, list[str]]:
    species_names = species_scope or list(TRAIT_DATASETS)
    return {
        species: _load_categories(species)
        for species in species_names
        if species in TRAIT_DATASETS and TRAIT_DATASETS[species].path.is_file()
    }


async def classify_trait2gene_query(message: str, llm: DeepSeekClient) -> dict[str, Any]:
    species_scope = _species_scope(message)
    if not species_scope:
        return {
            "selected": [],
            "top_k": _extract_top_k(message),
            "needs_species": True,
            "supported_species": SUPPORTED_SPECIES,
            "reason": "Trait2Gene 查询需要先明确物种。",
        }

    categories = available_trait_categories(species_scope)
    if not categories:
        return {
            "selected": [],
            "top_k": _extract_top_k(message),
            "species_scope": species_scope,
            "reason": "No trait2gene datasets are available.",
        }

    if not getattr(llm, "available", False):
        return _fallback_classification(message, categories)

    response = await chat_json(
        llm,
        [
            {
                "role": "system",
                "content": (
                    "你是 trait2gene 工具的性状分类器。"
                    "请根据当前用户问题，从给定 species 的 available_categories 中选择最匹配的 classify2 分类，"
                    "用于查询某个性状相关的基因。"
                    "只能选择候选列表里真实存在的分类，不要创造新分类。"
                    "如果用户询问多个性状，categories 可以包含多个分类。"
                    "match_mode 表示多个分类之间的关系：用户明确问同时/共同影响多个性状时用 all；"
                    "用户问宽泛上位性状、而多个分类只是候选构成项、近义项或产量构成项时用 any。"
                    "例如产量/yield 这类上位性状如果映射到百粒重、单株荚数、单荚粒数、植株重量等多个分类，必须用 any。"
                    "只有用户明确说同时包含、共同影响、兼具、both、simultaneously 等意思时，才使用 all。"
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
                            if item.species in species_scope
                        ],
                        "available_categories": categories,
                        "output_json_schema": {
                            "selected": [
                                {
                                    "species": "rice",
                                    "categories": ["soil salinity tolerance"],
                                    "match_mode": "all",
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
    )
    classification = _coerce_classification(response, categories)
    classification = _apply_default_match_mode(message, classification)
    if _has_invalid_categories(classification, categories) or not classification["selected"]:
        classification = await _repair_classification(message, classification, categories, llm)
        classification = _apply_default_match_mode(message, classification)
    return _drop_invalid_categories(classification, categories)


def run_trait2gene_query(message: str, classification: dict[str, Any]) -> dict[str, Any]:
    normalized = _coerce_classification(classification, available_trait_categories())
    normalized = _apply_default_match_mode(message, normalized)
    top_k = _clamp_top_k(normalized.get("top_k"))
    matches = []
    not_found = []

    if normalized.get("needs_species"):
        result = {
            "status": "need_user_input",
            "analysis": "trait2gene_query",
            "query": message,
            "classification": normalized,
            "top_k": top_k,
            "species_searched": [],
            "matches": [],
            "not_found": [],
            "message": "请先指定物种，例如水稻、玉米、大豆或拟南芥，再查询该性状相关基因。",
            "supported_species": normalized.get("supported_species", SUPPORTED_SPECIES),
        }
        return result

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

        match_mode = _normalize_match_mode(selection.get("match_mode"))
        result = _query_species(dataset, matched_categories, top_k, match_mode)
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

    result = {
        "status": "completed",
        "analysis": "trait2gene_query",
        "query": message,
        "classification": normalized,
        "top_k": top_k,
        "species_searched": [item["species"] for item in normalized.get("selected", [])],
        "matches": matches,
        "not_found": not_found,
    }
    return result


def clear_trait2gene_cache() -> None:
    _load_dataset.cache_clear()
    _load_categories.cache_clear()


def _query_species(dataset: TraitDataset, categories: list[str], top_k: int, match_mode: str) -> dict[str, Any]:
    df = _load_dataset(dataset.species)
    selected = df[df["category"].isin(categories)].copy()
    if selected.empty:
        gene_set: set[str] = set()
    elif match_mode == "any":
        gene_set = set(selected["gene_id"].dropna().astype(str))
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
            "match_mode": match_mode,
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
        "match_mode": match_mode,
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
        "evidence": _evidence_records(rows),
    }


def _evidence_records(rows: pd.DataFrame) -> list[dict[str, str]]:
    evidence = []
    seen = set()
    for row in rows.itertuples(index=False):
        item = {
            "category": _row_value(row, "category"),
            "trait": _shorten_text(_row_value(row, "trait"), 320),
            "literature": _row_value(row, "literature"),
            "source": _row_value(row, "source"),
        }
        key = (item["category"], item["trait"], item["literature"], item["source"])
        if key in seen:
            continue
        seen.add(key)
        if any(item.values()):
            evidence.append(item)
        if len(evidence) >= MAX_GENE_EVIDENCE:
            break
    return evidence


def _row_value(row: Any, name: str) -> str:
    value = getattr(row, name, "")
    return str(value).strip() if value is not None else ""


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
            raw_match_mode = item.get("match_mode") or payload.get("match_mode")
            clean_selected.append(
                {
                    "species": species,
                    "categories": valid_categories,
                    "match_mode": _normalize_match_mode(raw_match_mode) if raw_match_mode is not None else "",
                }
            )

    return {
        "selected": clean_selected,
        "top_k": _clamp_top_k(payload.get("top_k")),
        "needs_species": bool(payload.get("needs_species")),
        "supported_species": payload.get("supported_species") if isinstance(payload.get("supported_species"), list) else [],
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
            selected.append({"species": species, "categories": matches[:3], "match_mode": "all"})
    return {
        "selected": selected,
        "top_k": DEFAULT_TOP_K,
        "reason": "Matched literal classify2 text without LLM.",
    }


async def _repair_classification(
    message: str,
    classification: dict[str, Any],
    categories: dict[str, list[str]],
    llm: DeepSeekClient,
) -> dict[str, Any]:
    if not getattr(llm, "available", False):
        return classification
    response = await chat_json(
        llm,
        [
            {
                "role": "system",
                "content": (
                    "你是 trait2gene 分类结果修正器。"
                    "previous_classification 中可能包含不存在于某物种 available_categories 的分类，"
                    "请重新从真实候选中选择最接近用户问题的分类。"
                    "只能输出 available_categories 中逐字一致的分类。"
                    "如果用户问的是宽泛上位性状，而候选中只有多个构成性状或近义分类，"
                    "可以选择多个分类并将 match_mode 设为 any。"
                    "产量/yield 这类上位性状如果映射到百粒重、单株荚数、单荚粒数、植株重量等多个分类，必须用 any。"
                    "只有用户明确要求同时/共同满足多个性状时，match_mode 才设为 all。"
                    "如果某物种没有合适分类，就不要返回该物种。只输出 JSON。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "current_message": message,
                        "previous_classification": classification,
                    "invalid_categories": _invalid_categories(classification, categories),
                        "available_categories": categories,
                        "output_json_schema": {
                            "selected": [
                                {
                                    "species": "rice",
                                    "categories": ["grain number per panicle", "thousand-grain weight"],
                                    "match_mode": "any",
                                }
                            ],
                            "top_k": classification.get("top_k", DEFAULT_TOP_K),
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
    )
    return _coerce_classification(response, categories)


def _has_invalid_categories(classification: dict[str, Any], categories: dict[str, list[str]]) -> bool:
    return bool(_invalid_categories(classification, categories))


def _invalid_categories(classification: dict[str, Any], categories: dict[str, list[str]]) -> list[dict[str, Any]]:
    invalid = []
    for item in classification.get("selected", []):
        if not isinstance(item, dict):
            continue
        species = str(item.get("species", "")).strip().lower()
        available = set(categories.get(species, []))
        missing = [
            str(category)
            for category in item.get("categories", [])
            if str(category).strip() and str(category).strip() not in available
        ]
        if missing:
            invalid.append({"species": species, "categories": missing})
    return invalid


def _drop_invalid_categories(classification: dict[str, Any], categories: dict[str, list[str]]) -> dict[str, Any]:
    selected = []
    for item in classification.get("selected", []):
        if not isinstance(item, dict):
            continue
        species = str(item.get("species", "")).strip().lower()
        available = set(categories.get(species, []))
        valid = []
        seen = set()
        for category in item.get("categories", []):
            category_text = str(category).strip()
            if category_text in available and category_text not in seen:
                seen.add(category_text)
                valid.append(category_text)
        if valid:
            selected.append(
                {
                    "species": species,
                    "categories": valid,
                    "match_mode": _normalize_match_mode(item.get("match_mode")),
                }
            )
    return {**classification, "selected": selected}


def _normalize_match_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "any" if text in {"any", "or", "union"} else "all"


def _apply_default_match_mode(message: str, classification: dict[str, Any]) -> dict[str, Any]:
    selected = []
    explicit_all = _explicit_intersection_requested(message)
    for item in classification.get("selected", []):
        if not isinstance(item, dict):
            continue
        mode = str(item.get("match_mode") or "").strip().lower()
        if mode not in {"all", "any"}:
            mode = "all" if explicit_all else "any"
        selected.append({**item, "match_mode": mode})
    return {**classification, "selected": selected}


def _explicit_intersection_requested(message: str) -> bool:
    return bool(
        re.search(
            r"同时|共同|兼具|都(?:.+)?相关|同时包含|共同包含|both|simultaneous|simultaneously|at the same time",
            message,
            re.I,
        )
    )


def _species_scope(message: str) -> list[str]:
    species = []
    if re.search(r"拟南芥|arabidopsis thaliana|arabidopsis|tair|\bath\b", message, re.I):
        species.append("ath")
    if re.search(r"水稻|rice|oryza|oryza sativa", message, re.I):
        species.append("rice")
    if re.search(r"玉米|maize|corn|zea|zea mays", message, re.I):
        species.append("maize")
    if re.search(r"大豆|soy|soybean|glycine max", message, re.I):
        species.append("soy")
    return species


def _extract_top_k(message: str) -> int:
    for pattern in (
        r"\btop\s*[-_ ]?\s*(\d{1,3})\b",
        r"前\s*(\d{1,3})\s*(?:个|条|项)?",
        r"(\d{1,3})\s*(?:个|条|项).{0,8}(?:基因|genes)",
    ):
        match = re.search(pattern, message, re.I)
        if match:
            return _clamp_top_k(match.group(1))
    return DEFAULT_TOP_K


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

from __future__ import annotations

import json
import re
from heapq import nlargest
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.dataset as ds

from backend.app.config import DATA_DIR
from backend.app.services.id_mapping import with_id_mapping_summary


PREDICTION_DATASETS = {
    "maize": DATA_DIR / "GenePredictor" / "maize_lte_result.parquet",
    "rice": DATA_DIR / "GenePredictor" / "rice_lte_result.parquet",
}

PREDICTION_CSV_FALLBACKS = {
    "maize": DATA_DIR / "GenePredictor" / "maize_lte_result.csv",
    "rice": DATA_DIR / "GenePredictor" / "rice_lte_result.csv",
}

GENE_TRANS_PATHS = {
    "maize": DATA_DIR / "gene_trans" / "maize_gene_trans.json",
    "rice": DATA_DIR / "gene_trans" / "rice_gene_trans.json",
}

SPECIES_LABELS = {
    "maize": "玉米",
    "rice": "水稻",
}

DEFAULT_TOP_K = 5
MAX_TOP_K = 50
GENE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_.-])[A-Za-z][A-Za-z0-9_.-]{1,60}(?![A-Za-z0-9_.-])")
CSV_CHUNK_SIZE = 500_000


def run_gene_phenotype_prediction(message: str) -> dict[str, Any]:
    top_k = _extract_top_k(message)
    species_scope = _species_scope(message)
    terms = _extract_gene_terms(message)
    if not terms:
        return {
            "error": "没有识别到可查询的基因 ID 或别名。请提供水稻或玉米基因 ID，例如 LOC_Os07g48050 或 Zm00001eb123456。",
            "analysis": "gene_phenotype_prediction",
            "query": message,
        }

    resolved = _resolve_terms(terms, species_scope)
    if not resolved:
        return {
            "status": "completed",
            "analysis": "gene_phenotype_prediction",
            "query": message,
            "top_k": top_k,
            "species_searched": species_scope,
            "gene_mappings": [],
            "matches": [],
            "not_found": [{"input": term, "reason": "没有在水稻/玉米基因映射中找到对应标准 ID"} for term in terms],
        }

    matches = []
    not_found = []
    seen_queries: set[tuple[str, str]] = set()
    for item in resolved:
        key = (item["species"], item["query_id"])
        if key in seen_queries:
            continue
        seen_queries.add(key)
        predictions = _query_predictions(item["species"], item["query_id"], top_k)
        if predictions:
            matches.append(
                {
                    "input": item["input"],
                    "species": item["species"],
                    "species_label": SPECIES_LABELS.get(item["species"], item["species"]),
                    "canonical_id": item["canonical_id"],
                    "query_id": item["query_id"],
                    "matched_by": item["matched_by"],
                    "top_k": top_k,
                    "predictions": predictions,
                    "source_file": str(PREDICTION_DATASETS[item["species"]]),
                }
            )
        else:
            not_found.append(
                {
                    "input": item["input"],
                    "species": item["species"],
                    "canonical_id": item["canonical_id"],
                    "reason": "预测结果表中没有该基因的记录",
                }
            )

    return with_id_mapping_summary(
        {
            "status": "completed",
            "analysis": "gene_phenotype_prediction",
            "query": message,
            "top_k": top_k,
            "species_searched": species_scope,
            "genes": sorted({match["canonical_id"] for match in matches}),
            "gene_mappings": _unique_gene_mappings(resolved),
            "matches": matches,
            "not_found": not_found,
        }
    )


def _unique_gene_mappings(items: list[dict[str, str]]) -> list[dict[str, str]]:
    mappings = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        key = (item["input"], item["species"], item["canonical_id"])
        if key in seen:
            continue
        seen.add(key)
        mappings.append(
            {
                "input": item["input"],
                "species": item["species"],
                "species_label": SPECIES_LABELS.get(item["species"], item["species"]),
                "canonical_id": item["canonical_id"],
                "query_id": item["query_id"],
                "matched_by": item["matched_by"],
            }
        )
    return mappings


def _extract_top_k(message: str) -> int:
    patterns = (
        r"\btop\s*[-_ ]?\s*(\d{1,2})\b",
        r"前\s*(\d{1,2})\s*(?:个|条|项)?",
        r"(\d{1,2})\s*(?:个|条|项).{0,8}(?:性状|表型)",
    )
    for pattern in patterns:
        match = re.search(pattern, message, re.I)
        if match:
            return max(1, min(MAX_TOP_K, int(match.group(1))))
    return DEFAULT_TOP_K


def _species_scope(message: str) -> list[str]:
    text = message.lower()
    species = []
    if re.search(r"水稻|rice|oryza|loc_os|agis_os|rap[-_ ]?db", message, re.I):
        species.append("rice")
    if re.search(r"玉米|maize|corn|zea|zm\d+", message, re.I):
        species.append("maize")
    return species or ["rice", "maize"]


def _extract_gene_terms(message: str) -> list[str]:
    terms = []
    seen = set()
    for match in GENE_TOKEN_RE.finditer(message):
        term = match.group(0).strip(".,;:，。；：()[]{}<>\"'")
        if not term:
            continue
        lower = term.lower()
        if lower.isdigit():
            continue
        if lower in seen:
            continue
        seen.add(lower)
        terms.append(term)
    return terms


def _resolve_terms(terms: list[str], species_scope: list[str]) -> list[dict[str, str]]:
    resolved = []
    trans_cache: dict[str, dict[str, str]] = {}
    for term in terms:
        lower = term.lower()
        for species in species_scope:
            trans = trans_cache.setdefault(species, _load_trans(species))
            mapped = trans.get(lower)
            if mapped:
                resolved.append(
                    {
                        "input": term,
                        "species": species,
                        "canonical_id": mapped,
                        "query_id": mapped.lower(),
                        "matched_by": "gene_trans",
                    }
                )
                continue
            direct = _direct_query_id(term, species)
            if direct is not None:
                resolved.append(
                    {
                        "input": term,
                        "species": species,
                        "canonical_id": term,
                        "query_id": direct,
                        "matched_by": "direct_id",
                    }
                )
    return resolved


def _load_trans(species: str) -> dict[str, str]:
    path = GENE_TRANS_PATHS.get(species)
    if path is None or not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        return {}
    return {str(key).lower(): str(value) for key, value in payload.items() if value}


def _direct_query_id(term: str, species: str) -> str | None:
    lower = term.lower()
    if species == "maize" and re.match(r"^zm\d+[a-z0-9_.-]*$", lower):
        return lower
    if species == "rice" and re.match(r"^agis_os\d+g\d+$", lower):
        return lower
    return None


def _query_predictions(species: str, gene_id: str, top_k: int) -> list[dict[str, Any]]:
    path = PREDICTION_DATASETS.get(species)
    if path is not None and path.is_file() and path.suffix.lower() == ".parquet":
        return _query_predictions_from_parquet(path, gene_id, top_k)
    if path is not None and path.is_file() and path.suffix.lower() == ".csv":
        return _query_predictions_from_csv(path, gene_id, top_k)

    csv_path = PREDICTION_CSV_FALLBACKS.get(species)
    if csv_path is not None and csv_path.is_file():
        return _query_predictions_from_csv(csv_path, gene_id, top_k)

    return []


def _query_predictions_from_parquet(path: Path, gene_id: str, top_k: int) -> list[dict[str, Any]]:
    dataset = ds.dataset(path, format="parquet")
    table = dataset.to_table(
        columns=["phenotype", "pred_score"],
        filter=ds.field("gene_id") == gene_id,
    )
    rows = [
        {
            "phenotype": str(phenotype).strip(),
            "pred_score": float(score),
        }
        for phenotype, score in zip(table.column("phenotype").to_pylist(), table.column("pred_score").to_pylist())
        if score is not None
    ]
    return _rank_predictions(rows, top_k)


def _query_predictions_from_csv(path: Path, gene_id: str, top_k: int) -> list[dict[str, Any]]:
    rows = []
    for chunk in pd.read_csv(path, usecols=["gene_id", "phenotype", "pred_score"], chunksize=CSV_CHUNK_SIZE):
        chunk["gene_id"] = chunk["gene_id"].astype(str).str.strip().str.lower()
        matched = chunk.loc[chunk["gene_id"] == gene_id, ["phenotype", "pred_score"]].copy()
        if matched.empty:
            continue
        matched["pred_score"] = pd.to_numeric(matched["pred_score"], errors="coerce")
        matched = matched.dropna(subset=["pred_score"])
        for row in matched.itertuples(index=False):
            rows.append(
                {
                    "phenotype": str(row.phenotype).strip(),
                    "pred_score": float(row.pred_score),
                }
            )

    return _rank_predictions(rows, top_k)


def _rank_predictions(rows: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    ranked = nlargest(top_k, rows, key=lambda item: item["pred_score"])
    return [
        {
            "rank": index,
            "phenotype": item["phenotype"],
            "pred_score": item["pred_score"],
        }
        for index, item in enumerate(ranked, start=1)
    ]

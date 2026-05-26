from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.dataset as ds

from backend.app.config import DATA_DIR
from backend.app.services.id_mapping import with_id_mapping_summary


@dataclass(frozen=True)
class MutantDataset:
    species: str
    species_label: str
    database: str
    path: Path
    gene_column: str
    record_fields: dict[str, str]
    purchase_url_template: str = ""
    read_full_table: bool = False


MUTANT_DATASETS = {
    "ath": MutantDataset(
        species="ath",
        species_label="Arabidopsis",
        database="ABRC/NASC",
        path=DATA_DIR / "mutant_db" / "ath_abrc.parquet",
        gene_column="gene_id",
        record_fields={
            "gene_id": "gene_id",
            "url": "url",
            "stock_number": "Name / Stock Number",
            "nasc_stock_number": "NASC stock number",
            "price": "Base / Commercial Price",
            "description": "description",
        },
    ),
    "rice": MutantDataset(
        species="rice",
        species_label="rice",
        database="BGBIO",
        path=DATA_DIR / "mutant_db" / "rice_bgbio.parquet",
        gene_column="基因号",
        record_fields={
            "germplasm_type": "种质类型",
            "species_or_variety": "物种/品种",
            "vector": "载体骨架",
            "gene_id": "基因号",
            "target_sequence": "靶点序列",
            "validation": "鉴定分析",
        },
        purchase_url_template="https://www.seedseek.cn/?locus={gene_id}",
        read_full_table=True,
    ),
    "maize": MutantDataset(
        species="maize",
        species_label="maize",
        database="Maize EMS DB",
        path=DATA_DIR / "mutant_db" / "maize_ems.parquet",
        gene_column="GeneID",
        record_fields={
            "price": "Price",
            "gene_id": "GeneID",
            "chromosome": "Chr",
            "position": "Loc",
            "reference": "Ref",
            "mutation": "Mut",
            "transcript": "Transcript",
            "effect": "Effect",
            "codon_change": "Codon_Change",
            "aa_change": "AA_Change",
            "mutant_sample": "Mut_Sample",
        },
    ),
}

GENE_TRANS_PATHS = {
    "ath": DATA_DIR / "mutant_db" / "ath_gene_trans.json",
    "rice": DATA_DIR / "mutant_db" / "rice_gene_trans.json",
    "soy": DATA_DIR / "mutant_db" / "soy_gene_trans.json",
    "maize": DATA_DIR / "mutant_db" / "maize_gene_trans.json",
}

SPECIES_LABELS = {
    "ath": "Arabidopsis",
    "rice": "rice",
    "soy": "soybean",
    "maize": "maize",
}

DEFAULT_RECORD_LIMIT = 30
MAX_RECORD_LIMIT = 100
GENE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_.-])[A-Za-z][A-Za-z0-9_.-]{1,80}(?![A-Za-z0-9_.-])")


def run_gene_mutant_query(message: str) -> dict[str, Any]:
    record_limit = _extract_record_limit(message)
    terms = _extract_gene_terms(message)
    species_scope = _species_scope(message, terms)
    if not terms:
        return {
            "status": "completed",
            "analysis": "gene_mutant_query",
            "query": message,
            "species_searched": species_scope,
            "gene_mappings": [],
            "matches": [],
            "not_found": [{"input": message, "reason": "No gene-like ID or alias was recognized."}],
        }

    resolved = _resolve_terms(terms, species_scope)
    matches = []
    not_found = []
    seen_queries: set[tuple[str, str]] = set()

    for item in resolved:
        key = (item["species"], item["query_id"].lower())
        if key in seen_queries:
            continue
        seen_queries.add(key)
        dataset = MUTANT_DATASETS.get(item["species"])
        if dataset is None:
            not_found.append(
                {
                    "input": item["input"],
                    "species": item["species"],
                    "species_label": SPECIES_LABELS.get(item["species"], item["species"]),
                    "canonical_id": item["canonical_id"],
                    "reason": "No mutant database is configured for this species.",
                }
            )
            continue

        records, total_hits = _query_dataset(dataset, item["query_id"], record_limit)
        if total_hits:
            matches.append(
                {
                    "input": item["input"],
                    "species": item["species"],
                    "species_label": dataset.species_label,
                    "database": dataset.database,
                    "canonical_id": item["canonical_id"],
                    "query_id": item["query_id"],
                    "matched_by": item["matched_by"],
                    "has_mutant": True,
                    "total_hits": total_hits,
                    "returned_records": len(records),
                    "purchase_url": _purchase_url(dataset, item["query_id"]),
                    "records": records,
                    "source_file": str(dataset.path),
                }
            )
        else:
            not_found.append(
                {
                    "input": item["input"],
                    "species": item["species"],
                    "species_label": dataset.species_label,
                    "database": dataset.database,
                    "canonical_id": item["canonical_id"],
                    "query_id": item["query_id"],
                    "reason": "No mutant record was found for this gene in the configured database.",
                }
            )

    resolved_keys = {(item["input"].lower(), item["species"]) for item in resolved}
    for term in terms:
        if not any(key[0] == term.lower() for key in resolved_keys):
            not_found.append({"input": term, "reason": "No matching gene mapping was found."})

    return with_id_mapping_summary(
        {
            "status": "completed",
            "analysis": "gene_mutant_query",
            "query": message,
            "record_limit": record_limit,
            "species_searched": species_scope,
            "genes": sorted({match["canonical_id"] for match in matches}),
            "gene_mappings": _unique_gene_mappings(resolved),
            "matches": matches,
            "not_found": _dedupe_not_found(not_found),
        }
    )


def _extract_record_limit(message: str) -> int:
    match = re.search(r"\b(?:top|limit)\s*[-_ ]?\s*(\d{1,3})\b", message, re.I)
    if not match:
        return DEFAULT_RECORD_LIMIT
    return max(1, min(MAX_RECORD_LIMIT, int(match.group(1))))


def _extract_gene_terms(message: str) -> list[str]:
    terms = []
    seen = set()
    for match in GENE_TOKEN_RE.finditer(message):
        term = match.group(0).strip(".,;:，。；：?!?)()]{}<>\"'")
        if not term:
            continue
        if not any(ch.isdigit() for ch in term) and "_" not in term and "." not in term:
            continue
        lower = term.lower()
        if lower in seen:
            continue
        seen.add(lower)
        terms.append(term)
    return terms


def _species_scope(message: str, terms: list[str]) -> list[str]:
    species = []
    if re.search(r"arabidopsis|拟南芥|ath|abrc|nasc|at\d+g\d+", message, re.I):
        species.append("ath")
    if re.search(r"rice|oryza|水稻|loc_os|agis_os|rap[-_ ]?db|os\d+g\d+", message, re.I):
        species.append("rice")
    if re.search(r"maize|corn|zea|玉米|zm\d+", message, re.I):
        species.append("maize")
    if re.search(r"soy|soybean|大豆|glyma|gmw82", message, re.I):
        species.append("soy")
    if species:
        return species
    for term in terms:
        direct_species = _direct_species(term)
        if direct_species and direct_species not in species:
            species.append(direct_species)
    return species or ["ath", "rice", "maize"]


def _direct_species(term: str) -> str | None:
    lower = term.lower()
    if re.match(r"^at\d+g\d+", lower):
        return "ath"
    if re.match(r"^(loc_os|agis_os|os\d+g)", lower):
        return "rice"
    if re.match(r"^zm\d+", lower):
        return "maize"
    if re.match(r"^(glyma|gmw82)", lower):
        return "soy"
    return None


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
                        "species_label": SPECIES_LABELS.get(species, species),
                        "canonical_id": mapped,
                        "query_id": mapped,
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
                        "species_label": SPECIES_LABELS.get(species, species),
                        "canonical_id": direct,
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
    if species == "ath" and re.match(r"^at\d+g\d+", lower):
        return term.upper()
    if species == "rice" and re.match(r"^loc_os\d+g\d+$", lower):
        return _normalize_rice_loc(term)
    if species == "maize" and re.match(r"^zm\d+[a-z0-9_.-]*$", lower):
        return "Zm" + term[2:].lower()
    if species == "soy" and re.match(r"^glyma\.\d+g\d+", lower):
        return _normalize_soy_gene(term)
    return None


def _normalize_rice_loc(term: str) -> str:
    match = re.match(r"^loc_os(\d+)g(\d+)$", term, re.I)
    if not match:
        return term
    return f"LOC_Os{match.group(1)}g{match.group(2)}"


def _normalize_soy_gene(term: str) -> str:
    match = re.match(r"^glyma\.(\d+)g(\d+)$", term, re.I)
    if not match:
        return term
    return f"Glyma.{match.group(1)}G{match.group(2)}"


def _query_dataset(dataset: MutantDataset, gene_id: str, limit: int) -> tuple[list[dict[str, Any]], int]:
    if not dataset.path.is_file():
        return [], 0
    if dataset.read_full_table:
        return _query_full_table(dataset, gene_id, limit)
    return _query_parquet_filter(dataset, gene_id, limit)


def _purchase_url(dataset: MutantDataset, gene_id: str) -> str | None:
    if not dataset.purchase_url_template:
        return None
    return dataset.purchase_url_template.format(gene_id=gene_id)


def _query_parquet_filter(dataset: MutantDataset, gene_id: str, limit: int) -> tuple[list[dict[str, Any]], int]:
    table = ds.dataset(dataset.path, format="parquet").to_table(
        filter=ds.field(dataset.gene_column) == gene_id,
    )
    frame = table.to_pandas()
    return _frame_to_records(frame, dataset, limit), int(len(frame))


def _query_full_table(dataset: MutantDataset, gene_id: str, limit: int) -> tuple[list[dict[str, Any]], int]:
    frame = pd.read_parquet(dataset.path)
    matched = frame.loc[frame[dataset.gene_column].astype(str).str.lower() == gene_id.lower()].copy()
    return _frame_to_records(matched, dataset, limit), int(len(matched))


def _frame_to_records(frame: pd.DataFrame, dataset: MutantDataset, limit: int) -> list[dict[str, Any]]:
    records = []
    for _, row in frame.head(limit).iterrows():
        record = {"database": dataset.database, "species": dataset.species}
        for output_key, column in dataset.record_fields.items():
            if column not in row:
                continue
            value = row[column]
            if pd.isna(value):
                value = None
            elif hasattr(value, "item"):
                value = value.item()
            record[output_key] = value
        records.append(record)
    return records


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
                "species_label": item.get("species_label") or SPECIES_LABELS.get(item["species"], item["species"]),
                "canonical_id": item["canonical_id"],
                "query_id": item["query_id"],
                "matched_by": item["matched_by"],
            }
        )
    return mappings


def _dedupe_not_found(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped = []
    seen = set()
    for item in items:
        key = (
            str(item.get("input", "")).lower(),
            str(item.get("species", "")),
            str(item.get("canonical_id", "")),
            str(item.get("reason", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped

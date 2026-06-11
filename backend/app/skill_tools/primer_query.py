from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import pyarrow.dataset as ds

from backend.app.config import DATA_DIR
from backend.app.services.deepseek_client import DeepSeekClient
from backend.app.services.id_mapping import with_id_mapping_summary


PRIMER_DATASETS = {
    "clone": DATA_DIR / "primers" / "clone.parquet",
    "mutant": DATA_DIR / "primers" / "mutant.parquet",
    "qpcr": DATA_DIR / "primers" / "qpcr.parquet",
}

GENE_TRANS_PATHS = {
    "ath": DATA_DIR / "gene_trans" / "ath_gene_trans.json",
    "rice": DATA_DIR / "gene_trans" / "rice_gene_trans.json",
    "maize": DATA_DIR / "gene_trans" / "maize_gene_trans.json",
    "soy": DATA_DIR / "gene_trans" / "soy_gene_trans.json",
}

SPECIES_LABELS = {
    "ath": "Arabidopsis",
    "rice": "rice",
    "maize": "maize",
    "soy": "soybean",
}

PRIMER_SOURCE_LABELS = {
    "mutant": "mutant screening / genotyping primers",
    "clone": "clone / CDS amplification primers",
    "qpcr": "qPCR primers",
}

SOURCE_ORDER = ["mutant", "clone", "qpcr"]
PRIMER_NOT_FOUND_REASON = (
    "No stored primer pair was found for this gene and primer source. "
    "The gene may be too short, have abnormal GC content (too high or too low), "
    "contain repetitive sequence, form hairpin structures or primer dimers, "
    "have mismatched Tm values, or have insufficient terminal stability, "
    "which can prevent valid primer design."
)
PRIMER_NOT_FOUND_REASON_ZH = (
    "该基因可能序列过短、GC含量异常（过高或过低）、存在重复序列、易形成发夹结构或引物二聚体、"
    "Tm值不匹配、末端稳定性不足，导致无法设计正确引物。"
)
DEFAULT_TOP_K = 10
MAX_TOP_K = 50
CLASSIFIER_MAX_TOKENS = 700
GENE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_.-])[A-Za-z][A-Za-z0-9_.-]{1,80}(?![A-Za-z0-9_.-])")

PRIMER_COLUMNS = [
    "gene",
    "primer_pair",
    "forward_sequence",
    "forward_length",
    "forward_tm",
    "forward_gc",
    "forward_self_complementarity",
    "forward_self_3_complementarity",
    "reverse_sequence",
    "reverse_length",
    "reverse_tm",
    "reverse_gc",
    "reverse_self_complementarity",
    "reverse_self_3_complementarity",
    "product_length",
    "gene_lower",
]


async def classify_primer_query(message: str, llm: DeepSeekClient) -> dict[str, Any]:
    if not getattr(llm, "available", False):
        return _fallback_classification(message)

    response = await llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "You parse primer query requests for a local primer database. "
                    "Return JSON only. Determine requested genes, species, primer_sources, and top_k. "
                    "Allowed species: ath, rice, maize, soy. "
                    "Allowed primer_sources: mutant, clone, qpcr, auto. "
                    "Use mutant for mutant screening, genotyping, homozygous screening, T-DNA/EMS validation. "
                    "Use qpcr for qPCR/RT-qPCR expression primers. "
                    "Use clone for cloning, CDS, ORF, full-length amplification. "
                    "Use auto when the user asks for primers/design primers but does not specify a purpose. "
                    "Do not answer the user; only produce a JSON object."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "current_message": message,
                        "output_json_schema": {
                            "genes": ["LOC_Os01g66100"],
                            "species": ["rice"],
                            "primer_sources": ["mutant"],
                            "top_k": 10,
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
    return _coerce_classification(_json_from_text(response), message)


def run_primer_query(message: str, classification: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = _coerce_classification(classification or _fallback_classification(message), message)
    terms = _merge_terms(normalized.get("genes", []), _extract_gene_terms(message))
    if not terms:
        return {
            "status": "need_user_input",
            "analysis": "primer_query",
            "query": message,
            "classification": normalized,
            "matches": [],
            "not_found": [],
            "error": "No queryable gene ID or alias was recognized. Please provide a gene ID or gene symbol.",
        }

    species_scope = normalized.get("species") or _species_scope(message) or list(GENE_TRANS_PATHS)
    requested_sources = normalized.get("primer_sources") or ["auto"]
    top_k = _clamp_top_k(normalized.get("top_k"))
    resolved = _resolve_terms(terms, species_scope)
    if not resolved:
        return {
            "status": "completed",
            "analysis": "primer_query",
            "query": message,
            "classification": normalized,
            "top_k": top_k,
            "requested_sources": requested_sources,
            "species_searched": species_scope,
            "genes": [],
            "gene_mappings": [],
            "matches": [],
            "not_found": [
                {
                    "input": term,
                    "species_searched": species_scope,
                    "reason": "No matching standard gene ID was found in gene_trans mapping or direct ID rules.",
                }
                for term in terms
            ],
        }

    matches: list[dict[str, Any]] = []
    not_found: list[dict[str, Any]] = []
    seen_queries: set[tuple[str, str, str]] = set()

    for item in resolved:
        base_key = (item["species"], item["query_id"], ",".join(requested_sources))
        if base_key in seen_queries:
            continue
        seen_queries.add(base_key)
        if "auto" in requested_sources:
            auto_hit = False
            for source in SOURCE_ORDER:
                total_hits, primers = _query_source(source, item["query_id"], top_k)
                if total_hits:
                    matches.append(_match_record(item, source, total_hits, primers))
                    auto_hit = True
                    break
            if not auto_hit:
                not_found.append(_not_found_record(item, SOURCE_ORDER))
            continue

        found_any = False
        for source in requested_sources:
            total_hits, primers = _query_source(source, item["query_id"], top_k)
            if total_hits:
                matches.append(_match_record(item, source, total_hits, primers))
                found_any = True
            else:
                not_found.append(_not_found_record(item, [source]))
        if found_any:
            not_found = [
                record
                for record in not_found
                if not (
                    record.get("input") == item["input"]
                    and record.get("species") == item["species"]
                    and record.get("query_id") == item["query_id"]
                    and len(requested_sources) == 1
                )
            ]

    return with_id_mapping_summary(
        {
            "status": "completed",
            "analysis": "primer_query",
            "query": message,
            "classification": normalized,
            "top_k": top_k,
            "requested_sources": requested_sources,
            "species_searched": species_scope,
            "genes": sorted({match["canonical_id"] for match in matches}),
            "gene_mappings": _unique_gene_mappings(resolved),
            "matches": matches,
            "not_found": not_found,
        }
    )


def _match_record(item: dict[str, str], source: str, total_hits: int, primers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "input": item["input"],
        "species": item["species"],
        "species_label": SPECIES_LABELS.get(item["species"], item["species"]),
        "canonical_id": item["canonical_id"],
        "query_id": item["query_id"],
        "matched_by": item["matched_by"],
        "primer_source": source,
        "primer_source_label": PRIMER_SOURCE_LABELS[source],
        "total_hits": total_hits,
        "returned_primers": len(primers),
        "primers": primers,
        "source_file": str(PRIMER_DATASETS[source]),
        "note": "Product length is the precomputed PCR amplicon length from the primer database.",
    }


def _not_found_record(item: dict[str, str], sources: list[str]) -> dict[str, Any]:
    return {
        "input": item["input"],
        "species": item["species"],
        "species_label": SPECIES_LABELS.get(item["species"], item["species"]),
        "canonical_id": item["canonical_id"],
        "query_id": item["query_id"],
        "primer_sources": sources,
        "reason": PRIMER_NOT_FOUND_REASON,
        "reason_zh": PRIMER_NOT_FOUND_REASON_ZH,
    }


def _unique_gene_mappings(items: list[dict[str, str]]) -> list[dict[str, str]]:
    result = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in items:
        key = (item["input"].lower(), item["species"], item["canonical_id"].lower(), item["matched_by"])
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "input": item["input"],
                "species": item["species"],
                "species_label": SPECIES_LABELS.get(item["species"], item["species"]),
                "canonical_id": item["canonical_id"],
                "query_id": item["query_id"],
                "matched_by": item["matched_by"],
            }
        )
    return result


def _query_source(source: str, query_id: str, top_k: int) -> tuple[int, list[dict[str, Any]]]:
    path = PRIMER_DATASETS.get(source)
    if path is None or not path.is_file():
        return 0, []
    dataset = ds.dataset(path, format="parquet")
    table = dataset.to_table(columns=PRIMER_COLUMNS, filter=ds.field("gene_lower") == query_id)
    total_hits = table.num_rows
    if total_hits == 0:
        return 0, []
    rows = table.slice(0, top_k).to_pylist()
    return total_hits, [_primer_record(row) for row in rows]


def _primer_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "primer_pair": _clean_number(row.get("primer_pair")),
        "forward_sequence": _clean_text(row.get("forward_sequence")),
        "forward_length": _clean_number(row.get("forward_length")),
        "forward_tm": _clean_number(row.get("forward_tm")),
        "forward_gc": _clean_number(row.get("forward_gc")),
        "forward_self_complementarity": _clean_number(row.get("forward_self_complementarity")),
        "forward_self_3_complementarity": _clean_number(row.get("forward_self_3_complementarity")),
        "reverse_sequence": _clean_text(row.get("reverse_sequence")),
        "reverse_length": _clean_number(row.get("reverse_length")),
        "reverse_tm": _clean_number(row.get("reverse_tm")),
        "reverse_gc": _clean_number(row.get("reverse_gc")),
        "reverse_self_complementarity": _clean_number(row.get("reverse_self_complementarity")),
        "reverse_self_3_complementarity": _clean_number(row.get("reverse_self_3_complementarity")),
        "product_length": _clean_number(row.get("product_length")),
    }


def _resolve_terms(terms: list[str], species_scope: list[str]) -> list[dict[str, str]]:
    resolved = []
    seen: set[tuple[str, str, str]] = set()
    trans_cache: dict[str, dict[str, str]] = {}
    for term in terms:
        lower = term.lower()
        for species in species_scope:
            trans = trans_cache.setdefault(species, _load_trans(species))
            mapped = trans.get(lower)
            if mapped and _valid_mapped_id(mapped, species):
                query_id = mapped.lower()
                key = (term.lower(), species, query_id)
                if key not in seen:
                    seen.add(key)
                    resolved.append(
                        {
                            "input": term,
                            "species": species,
                            "canonical_id": mapped,
                            "query_id": query_id,
                            "matched_by": "gene_trans",
                        }
                    )
                continue
            direct = _direct_canonical_id(term, species)
            if direct is None:
                continue
            key = (term.lower(), species, direct.lower())
            if key in seen:
                continue
            seen.add(key)
            resolved.append(
                {
                    "input": term,
                    "species": species,
                    "canonical_id": direct,
                    "query_id": direct.lower(),
                    "matched_by": "direct_id",
                }
            )
    return resolved


@lru_cache(maxsize=None)
def _load_trans(species: str) -> dict[str, str]:
    path = GENE_TRANS_PATHS.get(species)
    if path is None or not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        return {}
    return {str(key).lower(): str(value).strip() for key, value in payload.items() if str(value).strip()}


def clear_primer_query_cache() -> None:
    _load_trans.cache_clear()


def _valid_mapped_id(value: str, species: str) -> bool:
    text = str(value).strip()
    if species == "rice":
        return bool(re.match(r"^AGIS_Os\d+g\d+$", text, re.I))
    if species == "maize":
        return bool(re.match(r"^Zm\d+", text, re.I))
    if species == "ath":
        return bool(re.match(r"^AT(?:[1-5]|C|M)G\d+$", text, re.I))
    if species == "soy":
        return bool(re.match(r"^(Glyma\.|GLYMA_U)", text, re.I))
    return False


def _direct_canonical_id(term: str, species: str) -> str | None:
    text = term.strip()
    lower = text.lower()
    if species == "rice" and re.match(r"^agis_os\d+g\d+$", lower):
        return _normalize_agis(text)
    if species == "maize" and re.match(r"^zm\d+[a-z0-9_.-]*$", lower):
        return "Zm" + text[2:]
    if species == "ath" and re.match(r"^at(?:[1-5]|c|m)g\d+$", lower):
        return text.upper()
    if species == "soy":
        if re.match(r"^glyma\.\d+g\d+", lower):
            return _normalize_glyma(text)
        if re.match(r"^gmw82\.\d+g\d+", lower):
            return _normalize_gmw82(text)
        if re.match(r"^glyma_u\d+$", lower):
            return text.upper()
    return None


def _normalize_agis(value: str) -> str:
    match = re.match(r"^agis_os(\d+)g(\d+)$", value, re.I)
    if not match:
        return value
    return f"AGIS_Os{match.group(1)}g{match.group(2)}"


def _normalize_glyma(value: str) -> str:
    match = re.match(r"^glyma\.(\d+)g(\d+.*)$", value, re.I)
    if not match:
        return value
    return f"Glyma.{match.group(1)}G{match.group(2)}"


def _normalize_gmw82(value: str) -> str:
    match = re.match(r"^gmw82\.(\d+)g(\d+.*)$", value, re.I)
    if not match:
        return value
    return f"GmW82.{match.group(1)}G{match.group(2)}"


def _fallback_classification(message: str) -> dict[str, Any]:
    return {
        "genes": _extract_gene_terms(message),
        "species": _species_scope(message),
        "primer_sources": _fallback_sources(message),
        "top_k": _extract_top_k(message),
        "reason": "fallback parser",
    }


def _fallback_sources(message: str) -> list[str]:
    text = message.lower()
    if re.search(r"qpcr|rt[-_ ]?qpcr|定量|荧光定量|表达引物", message, re.I):
        return ["qpcr"]
    if re.search(r"克隆|cds|orf|full[-_ ]?length|全长|clone", message, re.I):
        return ["clone"]
    if re.search(r"突变体|筛选|鉴定|纯合|tdna|t-dna|ems|genotyp|homozyg", message, re.I):
        return ["mutant"]
    if "primer" in text or "pcr" in text or "引物" in message:
        return ["auto"]
    return ["auto"]


def _species_scope(message: str) -> list[str]:
    species = []
    if re.search(r"拟南芥|arabidopsis|tair|\bat\d+g\d+", message, re.I):
        species.append("ath")
    if re.search(r"水稻|rice|oryza|loc_os|agis_os|os\d+g\d+", message, re.I):
        species.append("rice")
    if re.search(r"玉米|maize|corn|zea|zm\d+", message, re.I):
        species.append("maize")
    if re.search(r"大豆|soy|soybean|glyma|gmw82", message, re.I):
        species.append("soy")
    return species


def _extract_gene_terms(message: str) -> list[str]:
    terms = []
    seen = set()
    for match in GENE_TOKEN_RE.finditer(message):
        term = match.group(0).strip(".,;:，。；：、()[]{}<>\"'")
        lower = term.lower()
        if not lower or lower.isdigit() or lower in seen:
            continue
        seen.add(lower)
        terms.append(term)
    return terms


def _coerce_classification(payload: dict[str, Any], message: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    genes = _string_list(payload.get("genes") or payload.get("gene_ids") or payload.get("gene_id"))
    species = [item for item in _string_list(payload.get("species")) if item in GENE_TRANS_PATHS]
    sources = [_normalize_source(item) for item in _string_list(payload.get("primer_sources") or payload.get("source"))]
    sources = [item for item in sources if item in {*SOURCE_ORDER, "auto"}]
    if not sources:
        sources = _fallback_sources(message)
    if "auto" in sources:
        sources = ["auto"]
    return {
        "genes": _merge_terms(genes, []),
        "species": species,
        "primer_sources": _dedupe(sources),
        "top_k": _clamp_top_k(payload.get("top_k")),
        "reason": str(payload.get("reason") or "").strip(),
    }


def _normalize_source(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"q_pcr", "q-pcr", "rt-qpcr", "rt_qpcr", "real-time-pcr"}:
        return "qpcr"
    if text in {"screen", "screening", "genotyping", "mutant_screening", "mutant-screening"}:
        return "mutant"
    if text in {"cloning", "cds", "orf", "full_length", "full-length"}:
        return "clone"
    return text


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _merge_terms(*groups: Any) -> list[str]:
    result = []
    seen = set()
    for group in groups:
        if isinstance(group, str):
            candidates = [group]
        elif isinstance(group, list):
            candidates = group
        else:
            continue
        for item in candidates:
            text = str(item).strip()
            lower = text.lower()
            if not text or lower in seen:
                continue
            seen.add(lower)
            result.append(text)
    return result


def _dedupe(items: list[str]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _extract_top_k(message: str) -> int:
    for pattern in (
        r"\btop\s*[-_ ]?\s*(\d{1,2})\b",
        r"前\s*(\d{1,2})\s*(?:对|个|条|项)?",
        r"(\d{1,2})\s*(?:对|个|条|项).{0,8}(?:引物|primer)",
    ):
        match = re.search(pattern, message, re.I)
        if match:
            return _clamp_top_k(match.group(1))
    return DEFAULT_TOP_K


def _clamp_top_k(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TOP_K
    return max(1, min(MAX_TOP_K, number))


def _json_from_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    match = re.search(r"\{.*\}", stripped, re.S)
    if match:
        stripped = match.group(0)
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("primer classifier response must be a JSON object")
    return value


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_number(value: Any) -> int | float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return int(number)
    return round(number, 3)

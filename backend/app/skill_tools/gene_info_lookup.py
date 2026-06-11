from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.app.config import DATA_DIR


GENE_TRANS_PATHS = {
    "ath": DATA_DIR / "gene_trans" / "ath_gene_trans.json",
    "rice": DATA_DIR / "gene_trans" / "rice_gene_trans.json",
    "maize": DATA_DIR / "gene_trans" / "maize_gene_trans.json",
    "soy": DATA_DIR / "gene_trans" / "soy_gene_trans.json",
}

GENE_INFO_PATHS = {
    "ath": DATA_DIR / "gene_info" / "ath_gene_info.json",
    "rice": DATA_DIR / "gene_info" / "rice_gene_info.json",
    "maize": DATA_DIR / "gene_info" / "maize_gene_info.json",
    "soy": DATA_DIR / "gene_info" / "soy_gene_info.json",
}

FUNCTION_LINE_RE = re.compile(
    r"(?:"
    r"gene\s*symbol|gene\s*name|description|summary|annotation|function|domain|"
    r"uniprot|uniport|eggnog|go\s*annotation|kegg|"
    r"蛋白功能|蛋白质名称|蛋白注释|结构域|功能|关联性状类别"
    r")",
    re.I,
)
MISSING_VALUE_RE = re.compile(r"(?:未发现|未找到|not found|nan|none|null)\s*$", re.I)
LOW_VALUE_LINE_RE = re.compile(r"(?:uniprot|uniport)\s*entry", re.I)
JSON_KEY_RE = re.compile(r'^\s*"((?:\\.|[^"])*)"\s*:')
MIN_SUMMARY_CHARS = 160
DEFAULT_SUMMARY_CHARS = 700
TOTAL_SUMMARY_BUDGET = 12000
MAX_SUMMARY_LINES = 6
MAX_QUERY_TERMS = 40
MAX_GENE_INFO_TEXT_CHARS = 3000


def enrich_blast_hits(hits: list[dict[str, Any]]) -> dict[str, int]:
    resolved: list[tuple[dict[str, Any], dict[str, str]]] = []
    wanted_by_species: dict[str, set[str]] = {}
    for hit in hits:
        mapping = resolve_gene_record(str(hit.get("species") or ""), str(hit.get("subject_id") or ""))
        if mapping is None:
            hit["gene_info"] = {
                "matched": False,
                "source_id": str(hit.get("subject_id") or ""),
                "reason": "No canonical gene ID mapping was found for this BLAST record.",
            }
            continue
        resolved.append((hit, mapping))
        wanted_by_species.setdefault(mapping["species"], set()).add(mapping["canonical_id"])

    summaries: dict[tuple[str, str], dict[str, Any]] = {}
    summary_chars = _summary_char_limit(len(hits))
    for species, canonical_ids in wanted_by_species.items():
        for canonical_id, text in _stream_gene_info(species, canonical_ids).items():
            summaries[(species, canonical_id.lower())] = compact_gene_info(text, summary_chars)

    matched = 0
    for hit, mapping in resolved:
        summary = summaries.get((mapping["species"], mapping["canonical_id"].lower()))
        if summary is None:
            hit["gene_info"] = {
                "matched": False,
                **mapping,
                "reason": "The canonical gene ID was resolved, but no local gene info record was found.",
            }
            continue
        matched += 1
        hit["gene_info"] = {
            "matched": True,
            **mapping,
            **summary,
        }
    return {
        "queried_hits": len(hits),
        "mapped_hits": len(resolved),
        "annotated_hits": matched,
        "summary_chars_per_hit": summary_chars,
    }


def resolve_gene_record(species: str, record_id: str) -> dict[str, str] | None:
    if species not in GENE_TRANS_PATHS or not record_id:
        return None
    trans = _load_trans(species)
    for candidate in _record_id_candidates(record_id):
        canonical_id = trans.get(candidate.lower())
        if canonical_id:
            return {
                "source_id": record_id,
                "species": species,
                "canonical_id": canonical_id,
                "matched_by": "gene_trans",
            }
        direct = _direct_canonical_id(candidate, species)
        if direct:
            return {
                "source_id": record_id,
                "species": species,
                "canonical_id": direct,
                "matched_by": "direct_id",
            }
    return None


def compact_gene_info(text: str, max_chars: int = DEFAULT_SUMMARY_CHARS) -> dict[str, Any]:
    candidates: list[tuple[int, int, str]] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip().lstrip("#").strip()
        if (
            not line
            or not FUNCTION_LINE_RE.search(line)
            or MISSING_VALUE_RE.search(line)
            or LOW_VALUE_LINE_RE.search(line)
        ):
            continue
        if not any(existing == line for _priority, _index, existing in candidates):
            candidates.append((_line_priority(line), len(candidates), line))
    selected = [line for _priority, _index, line in sorted(candidates)[:MAX_SUMMARY_LINES]]
    if not selected:
        selected = ["No concise functional annotation was found in the local gene info record."]
    summary = " | ".join(selected)
    if len(summary) > max_chars:
        summary = summary[: max(0, max_chars - 15)].rstrip() + "... <truncated>"
    return {
        "function_summary": summary,
    }


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


def _stream_gene_info(species: str, canonical_ids: set[str]) -> dict[str, str]:
    path = GENE_INFO_PATHS.get(species)
    if path is None or not path.is_file() or not canonical_ids:
        return {}
    wanted = {item.lower(): item for item in canonical_ids}
    found: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            match = JSON_KEY_RE.match(line)
            if match is None:
                continue
            key = json.loads(f'"{match.group(1)}"')
            requested = wanted.get(str(key).lower())
            if requested is None:
                continue
            payload = json.loads("{" + line.rstrip().rstrip(",") + "}")
            value = payload.get(key)
            if isinstance(value, str):
                found[requested] = value
            if len(found) == len(wanted):
                break
    return found


def _record_id_candidates(record_id: str) -> list[str]:
    original = str(record_id or "").strip().split()[0]
    candidates = [original]
    suffix_patterns = [
        r"\.\d+$",
        r"_t\d+$",
        r"_p\d+$",
        r"-t\d+$",
        r"-p\d+$",
    ]
    for pattern in suffix_patterns:
        stripped = re.sub(pattern, "", original, flags=re.I)
        if stripped != original:
            candidates.append(stripped)
    return _dedupe(candidates)


def _direct_canonical_id(candidate: str, species: str) -> str | None:
    text = str(candidate or "").strip()
    lower = text.lower()
    if species == "ath" and re.fullmatch(r"at(?:[1-5]|c|m)g\d+", lower):
        return text.upper()
    if species == "rice" and re.fullmatch(r"agis_os\d+g\d+", lower):
        return "AGIS_Os" + re.split(r"os", text, flags=re.I, maxsplit=1)[1]
    if species == "maize" and re.fullmatch(r"zm\d+[a-z0-9_.-]*", lower):
        return "Zm" + text[2:]
    if species == "soy" and re.fullmatch(r"glyma\.\d+g\d+", lower):
        return "Glyma." + text.split(".", 1)[1].upper()
    return None


def _summary_char_limit(hit_count: int) -> int:
    if hit_count <= 0:
        return DEFAULT_SUMMARY_CHARS
    return max(MIN_SUMMARY_CHARS, min(DEFAULT_SUMMARY_CHARS, TOTAL_SUMMARY_BUDGET // hit_count))


def _line_priority(line: str) -> int:
    lowered = line.lower()
    if re.search(r"(?:function|功能|蛋白注释|curator summary)", lowered):
        return 0
    if re.search(r"(?:description|蛋白质名称|annotation|short description|gene name|gene symbol)", lowered):
        return 1
    if re.search(r"(?:domain|结构域)", lowered):
        return 2
    if "关联性状类别" in line:
        return 3
    if re.search(r"(?:go\s*注释|go\s*annotation|kegg)", lowered):
        return 4
    return 5


def _dedupe(items: list[str]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        lower = item.lower()
        if lower in seen:
            continue
        seen.add(lower)
        result.append(item)
    return result


def clear_gene_info_lookup_cache() -> None:
    _load_trans.cache_clear()


def run_gene_info_query(message: str) -> dict[str, Any]:
    terms = _extract_query_terms(message)
    species_scope = _species_scope(message)
    if not species_scope:
        species_scope = list(GENE_TRANS_PATHS)

    mappings: list[dict[str, str]] = []
    seen_mappings: set[tuple[str, str, str]] = set()
    for term in terms[:MAX_QUERY_TERMS]:
        for species in _species_for_term(term, species_scope):
            mapping = resolve_gene_record(species, term)
            if mapping is None:
                continue
            key = (term.lower(), mapping["species"], mapping["canonical_id"].lower())
            if key in seen_mappings:
                continue
            seen_mappings.add(key)
            mappings.append({"input": term, **mapping})

    wanted_by_species: dict[str, set[str]] = {}
    for mapping in mappings:
        wanted_by_species.setdefault(mapping["species"], set()).add(mapping["canonical_id"])

    info_by_key: dict[tuple[str, str], str] = {}
    for species, canonical_ids in wanted_by_species.items():
        for canonical_id, text in _stream_gene_info(species, canonical_ids).items():
            info_by_key[(species, canonical_id.lower())] = text

    matches: list[dict[str, Any]] = []
    not_found: list[dict[str, Any]] = []
    for mapping in mappings:
        text = info_by_key.get((mapping["species"], mapping["canonical_id"].lower()))
        if text is None:
            not_found.append(
                {
                    "input": mapping["input"],
                    "species": mapping["species"],
                    "canonical_id": mapping["canonical_id"],
                    "reason": "The canonical gene ID was resolved, but no local gene info record was found.",
                }
            )
            continue
        matches.append(
            {
                "input": mapping["input"],
                "species": mapping["species"],
                "matched": True,
                "matched_by": mapping["matched_by"],
                "canonical_id": mapping["canonical_id"],
                "text": _truncate_gene_info_text(text),
                **compact_gene_info(text),
            }
        )

    if not mappings:
        not_found = [
            {
                "input": term,
                "species_searched": species_scope,
                "reason": "No canonical gene ID mapping was found for this query term.",
            }
            for term in terms[:MAX_QUERY_TERMS]
        ]

    return {
        "status": "completed" if matches else "not_found",
        "analysis": "query_gene_info",
        "query": message,
        "query_terms": terms,
        "species_searched": species_scope,
        "gene_mappings": mappings,
        "matches": matches,
        "not_found": not_found,
    }


def _extract_query_terms(message: str) -> list[str]:
    terms = []
    stop_words = {
        "current",
        "research",
        "step",
        "question",
        "purpose",
        "dependency",
        "outputs",
        "summary",
        "structured_result",
        "candidate_gene_ids",
        "gene",
        "genes",
        "rice",
        "maize",
        "soy",
        "soybean",
        "arabidopsis",
    }
    for match in re.finditer(r"(?<![A-Za-z0-9_.-])[A-Za-z][A-Za-z0-9_.-]{2,80}(?![A-Za-z0-9_.-])", message or ""):
        term = match.group(0).strip(".,;:()[]{}<>\"'")
        if not term or term.lower() in stop_words:
            continue
        if _looks_like_gene_query_term(term):
            terms.append(term)
    return _dedupe(terms)


def _looks_like_gene_query_term(term: str) -> bool:
    return bool(_looks_like_canonical_gene_id(term) or re.fullmatch(r"[A-Za-z]{2,}\d+[A-Za-z0-9_-]*", term))


def _looks_like_canonical_gene_id(term: str) -> bool:
    return bool(
        re.search(
            r"^(AGIS_Os\d+g\d+|LOC_Os\d+g\d+|Os\d+g\d+|AT[1-5CM]G\d+|Zm\d+[A-Za-z0-9_.-]*|Glyma\.\d+G\d+|GmW82\.\d+G\d+)",
            term,
            re.I,
        )
    )


def _species_scope(message: str) -> list[str]:
    text = str(message or "").lower()
    scope: list[str] = []
    if re.search(r"rice|oryza|loc_os|agis_os|os\d+g|水稻", text, re.I):
        scope.append("rice")
    if re.search(r"maize|corn|zea|zm\d+|玉米", text, re.I):
        scope.append("maize")
    if re.search(r"soy|soybean|glyma|gmw82|大豆", text, re.I):
        scope.append("soy")
    if re.search(r"arabidopsis|tair|at[1-5cm]g|拟南芥", text, re.I):
        scope.append("ath")
    return _dedupe(scope)


def _species_for_term(term: str, species_scope: list[str]) -> list[str]:
    lower = term.lower()
    if re.match(r"^(agis_os|loc_os|os\d+g)", lower):
        return ["rice"]
    if re.match(r"^at[1-5cm]g", lower):
        return ["ath"]
    if re.match(r"^zm\d+", lower):
        return ["maize"]
    if re.match(r"^(glyma\.|gmw82\.)", lower):
        return ["soy"]
    return species_scope


def _truncate_gene_info_text(text: str) -> str:
    value = str(text or "")
    if len(value) <= MAX_GENE_INFO_TEXT_CHARS:
        return value
    return value[:MAX_GENE_INFO_TEXT_CHARS].rstrip() + "... <truncated>"

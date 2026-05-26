from __future__ import annotations

from typing import Any


def build_id_mapping_summary(gene_mappings: Any) -> list[dict[str, Any]]:
    if not isinstance(gene_mappings, list):
        return []

    summary: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in gene_mappings:
        if not isinstance(item, dict):
            continue
        source_id = _clean(item.get("input"))
        canonical_id = _clean(item.get("canonical_id"))
        if not source_id or not canonical_id:
            continue
        species = _clean(item.get("species"))
        query_id = _clean(item.get("query_id"))
        matched_by = _clean(item.get("matched_by")) or "unknown"
        key = (source_id.lower(), canonical_id.lower(), species.lower(), matched_by.lower())
        if key in seen:
            continue
        seen.add(key)
        summary.append(
            {
                "source_id": source_id,
                "canonical_id": canonical_id,
                "query_id": query_id,
                "species": species,
                "species_label": _clean(item.get("species_label")),
                "matched_by": matched_by,
                "message": _mapping_message(source_id, canonical_id, query_id, species, matched_by),
            }
        )
    return summary


def with_id_mapping_summary(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    summary = build_id_mapping_summary(result.get("gene_mappings"))
    if not summary:
        return result
    enriched = dict(result)
    enriched["id_mapping_performed"] = True
    enriched["id_mapping_summary"] = summary
    return enriched


def enrich_skill_output_with_id_mapping(skill_output: Any) -> Any:
    if not isinstance(skill_output, dict):
        return skill_output
    if "result" not in skill_output:
        return skill_output
    enriched_result = with_id_mapping_summary(skill_output.get("result"))
    if enriched_result is skill_output.get("result"):
        return skill_output
    return {**skill_output, "result": enriched_result}


def _mapping_message(
    source_id: str,
    canonical_id: str,
    query_id: str,
    species: str,
    matched_by: str,
) -> str:
    parts = [f"ID mapping applied: {source_id} -> {canonical_id}"]
    if query_id and query_id != canonical_id:
        parts.append(f"query_id={query_id}")
    if species:
        parts.append(f"species={species}")
    if matched_by:
        parts.append(f"matched_by={matched_by}")
    return "; ".join(parts)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()

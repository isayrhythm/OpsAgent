from __future__ import annotations

from typing import Any, Iterator

from backend.app.services.id_mapping import with_id_mapping_summary
from backend.app.services.result_evaluator import compact_value


def ui_block_events(skill_outputs: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for item in skill_outputs:
        result = item.get("output", {}).get("result")
        if not isinstance(result, dict):
            continue
        for block in result.get("ui_blocks", []):
            if not isinstance(block, dict) or block.get("type") != "gene_function_research_path":
                continue
            block_header = {key: value for key, value in block.items() if key != "steps"}
            yield {"action": "start", "block": block_header}
            for step in block.get("steps", []):
                if isinstance(step, dict):
                    yield {"action": "step", "block_id": block.get("id"), "step": step}


def answer_ready_output(value: Any) -> Any:
    if not isinstance(value, dict):
        return compact_value(value)
    result = value.get("result")
    result = with_id_mapping_summary(result)
    if result is not value.get("result"):
        value = {**value, "result": result}
    if isinstance(result, dict) and result.get("analysis") == "gene_phenotype_prediction":
        answer_result = {
            key: item
            for key, item in result.items()
            if key
            in {
                "status",
                "analysis",
                "query",
                "top_k",
                "species_searched",
                "genes",
                "not_found",
                "id_mapping_performed",
                "id_mapping_summary",
            }
        }
        answer_result["gene_mappings"] = result.get("gene_mappings", [])
        answer_result["matches"] = [
            {
                "input": match.get("input"),
                "species": match.get("species"),
                "species_label": match.get("species_label"),
                "canonical_id": match.get("canonical_id"),
                "matched_by": match.get("matched_by"),
                "top_k": match.get("top_k"),
                "predictions": [
                    {
                        "rank": item.get("rank"),
                        "phenotype": item.get("phenotype"),
                        "pred_score": item.get("pred_score"),
                    }
                    for item in match.get("predictions", [])
                    if isinstance(item, dict)
                ],
            }
            for match in result.get("matches", [])
            if isinstance(match, dict)
        ]
        return {**value, "result": answer_result}
    if isinstance(result, dict) and result.get("analysis") == "trait2gene_query":
        answer_result = {
            key: item
            for key, item in result.items()
            if key
            in {
                "status",
                "analysis",
                "query",
                "classification",
                "top_k",
                "species_searched",
                "not_found",
                "message",
                "supported_species",
            }
        }
        answer_result["answer_requirements"] = [
            "Use only literature/source/evidence returned in this result.",
            "For reported trait-associated genes, include literature evidence when available.",
            "Do not invent paper titles, authors, years, DOI, or sources.",
        ]
        answer_result["matches"] = [
            {
                "species": match.get("species"),
                "species_label": match.get("species_label"),
                "categories": match.get("categories", []),
                "match_mode": match.get("match_mode"),
                "total_genes": match.get("total_genes"),
                "returned_genes": match.get("returned_genes"),
                "source_counts": (match.get("source_counts") or [])[:6],
                "references": (match.get("references") or [])[:10],
                "genes": [
                    {
                        "gene_id": gene.get("gene_id"),
                        "gene_names": gene.get("gene_names") or [],
                        "categories": gene.get("categories") or [],
                        "evidence_count": gene.get("evidence_count"),
                        "sources": gene.get("sources") or [],
                        "references": (gene.get("references") or [])[:4],
                        "evidence": [
                            {
                                "category": item.get("category"),
                                "trait": item.get("trait"),
                                "literature": item.get("literature"),
                                "source": item.get("source"),
                            }
                            for item in (gene.get("evidence") or [])[:3]
                            if isinstance(item, dict)
                        ],
                    }
                    for gene in (match.get("genes") or [])[:12]
                    if isinstance(gene, dict)
                ],
            }
            for match in result.get("matches", [])
            if isinstance(match, dict)
        ]
        return compact_value({**value, "result": answer_result})
    if isinstance(result, dict) and result.get("analysis") == "primer_query":
        answer_result = {
            key: item
            for key, item in result.items()
            if key
            in {
                "status",
                "analysis",
                "query",
                "classification",
                "top_k",
                "requested_sources",
                "species_searched",
                "genes",
                "gene_mappings",
                "id_mapping_performed",
                "id_mapping_summary",
                "not_found",
            }
        }
        answer_result["answer_requirements"] = [
            "Present primer pairs in a concise table.",
            "Include forward/reverse sequences and product_length for each primer pair.",
            "Mention that product_length is the precomputed PCR amplicon length.",
            "If no primer was found, use reason_zh and do not invent primer sequences.",
        ]
        answer_result["matches"] = [
            {
                "input": match.get("input"),
                "species": match.get("species"),
                "species_label": match.get("species_label"),
                "canonical_id": match.get("canonical_id"),
                "query_id": match.get("query_id"),
                "matched_by": match.get("matched_by"),
                "primer_source": match.get("primer_source"),
                "primer_source_label": match.get("primer_source_label"),
                "total_hits": match.get("total_hits"),
                "returned_primers": match.get("returned_primers"),
                "note": match.get("note"),
                "primers": [
                    {
                        "primer_pair": primer.get("primer_pair"),
                        "forward_sequence": primer.get("forward_sequence"),
                        "forward_tm": primer.get("forward_tm"),
                        "forward_gc": primer.get("forward_gc"),
                        "reverse_sequence": primer.get("reverse_sequence"),
                        "reverse_tm": primer.get("reverse_tm"),
                        "reverse_gc": primer.get("reverse_gc"),
                        "product_length": primer.get("product_length"),
                    }
                    for primer in (match.get("primers") or [])[:12]
                    if isinstance(primer, dict)
                ],
            }
            for match in result.get("matches", [])
            if isinstance(match, dict)
        ]
        return compact_value({**value, "result": answer_result})
    if isinstance(result, dict) and result.get("analysis") == "blast_query":
        answer_result = {
            key: item
            for key, item in result.items()
            if key
            in {
                "status",
                "analysis",
                "query",
                "classification",
                "sequence_count",
                "species_searched",
                "gene_info_enrichment",
                "not_found",
                "errors",
            }
        }
        answer_result["answer_requirements"] = [
            "Group the BLAST results by query_label and present candidate homologous records in concise tables.",
            "Include subject_id, species, record_type, program, identity, query_coverage, evalue, and bitscore.",
            "For each hit, include the resolved canonical gene ID and compact local gene function summary when gene_info.matched is true.",
            "If gene_info.matched is false, state that no local functional annotation was resolved for that hit. Do not invent a function.",
            "A nucleotide database hit is usually a candidate gene record. A protein database hit may be a transcript or protein record. Do not claim that every subject_id is a standard gene ID.",
            "Mention queries with no passing hit and any execution errors. Do not invent annotations that were not returned by the tool.",
        ]
        answer_result["matches"] = [
            {
                "query_label": match.get("query_label"),
                "query_type": match.get("query_type"),
                "query_length": match.get("query_length"),
                "source": match.get("source"),
                "total_hits": match.get("total_hits"),
                "returned_hits": match.get("returned_hits"),
                "hits": [
                    {
                        "rank": hit.get("rank"),
                        "subject_id": hit.get("subject_id"),
                        "species": hit.get("species"),
                        "species_label": hit.get("species_label"),
                        "record_type": hit.get("record_type"),
                        "program": hit.get("program"),
                        "identity": hit.get("identity"),
                        "query_coverage": hit.get("query_coverage"),
                        "subject_coverage": hit.get("subject_coverage"),
                        "evalue": hit.get("best_evalue"),
                        "bitscore": hit.get("best_bitscore"),
                        "gene_info": hit.get("gene_info"),
                    }
                    for hit in (match.get("hits") or [])[:10]
                    if isinstance(hit, dict)
                ],
            }
            for match in result.get("matches", [])
            if isinstance(match, dict)
        ]
        return compact_value({**value, "result": answer_result})
    if not isinstance(result, dict) or not result.get("ui_blocks"):
        return compact_value(value)
    answer_result = {key: item for key, item in result.items() if key not in {"matches", "ui_blocks"}}
    answer_result["matches"] = [
        {
            "paper_id": match.get("paper_id"),
            "title": match.get("title"),
            "gene_id": match.get("gene_id"),
            "step_count": len(match.get("steps", [])),
        }
        for match in result.get("matches", [])
        if isinstance(match, dict)
    ]
    answer_result["visualized_ui_blocks"] = [
        {
            "type": block.get("type"),
            "gene_id": block.get("gene_id"),
            "title": block.get("title"),
            "step_count": len(block.get("steps", [])),
        }
        for block in result.get("ui_blocks", [])
        if isinstance(block, dict)
    ]
    return compact_value({**value, "result": answer_result})

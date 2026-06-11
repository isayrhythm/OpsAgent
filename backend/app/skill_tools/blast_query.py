from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.config import DATA_DIR
from backend.app.schemas import UploadedFileSummary
from backend.app.services.deepseek_client import DeepSeekClient
from backend.app.skill_tools.gene_info_lookup import enrich_blast_hits


BLAST_DB_ROOT = DATA_DIR / "blast_db"
BLAST_DATABASES = {
    "ath": BLAST_DB_ROOT / "Arabidopsis",
    "maize": BLAST_DB_ROOT / "Maize",
    "rice": BLAST_DB_ROOT / "Rice",
    "soy": BLAST_DB_ROOT / "Soybean",
}
SPECIES_LABELS = {
    "ath": "Arabidopsis",
    "maize": "maize",
    "rice": "rice",
    "soy": "soybean",
}
PROGRAMS = {
    "blastn": {"query_types": {"DNA", "RNA"}, "db": "nt", "record_type": "nucleotide_gene_record"},
    "blastp": {"query_types": {"PROTEIN"}, "db": "protein", "record_type": "protein_record"},
    "blastx": {"query_types": {"DNA", "RNA"}, "db": "protein", "record_type": "protein_record"},
    "tblastn": {"query_types": {"PROTEIN"}, "db": "nt", "record_type": "nucleotide_gene_record"},
}
DNA_IUPAC = set("ACGTNRYKMSWBDHV")
RNA_IUPAC = set("ACGUNRYKMSWBDHV")
PROTEIN_IUPAC = set("ACDEFGHIKLMNPQRSTVWYBXZJUO*")
FASTA_SUFFIXES = {".fa", ".fasta", ".fna", ".faa", ".fas"}
TRUNCATION_RE = re.compile(r"(?:<truncated|\[\s*truncated|\.{3}\s*truncated)", re.I)
SEQUENCE_TOKEN_RE = re.compile(r"(?<![A-Za-z])[A-Za-z*]{20,}(?![A-Za-z])")
OUTFMT_COLUMNS = [
    "qseqid",
    "sseqid",
    "stitle",
    "length",
    "nident",
    "mismatch",
    "gaps",
    "qstart",
    "qend",
    "sstart",
    "send",
    "evalue",
    "bitscore",
    "qlen",
    "slen",
]
OUTFMT = "6 " + " ".join(OUTFMT_COLUMNS)
DEFAULT_TOP_K = 5
MAX_TOP_K = 10
MAX_QUERY_SEQUENCES = 10
MIN_SEQUENCE_LENGTH = 5
MAX_SEQUENCE_LENGTH = 10000
BLAST_THREADS = 4
BLAST_TIMEOUT_SECONDS = 120
DEFAULT_EVALUE = 1e-10
MAX_PARALLEL_JOBS = 4
CLASSIFIER_MAX_TOKENS = 600


@dataclass(frozen=True)
class QuerySequence:
    query_id: str
    label: str
    sequence: str
    sequence_type: str
    source: str


async def classify_blast_query(message: str, llm: DeepSeekClient) -> dict[str, Any]:
    if not getattr(llm, "available", False):
        return _fallback_classification(message)
    response = await llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "Parse a local plant BLAST request. Return JSON only. "
                    "Choose species, program, top_k, and evalue. "
                    "Allowed species: ath, maize, rice, soy. "
                    "Allowed programs: auto, blastn, blastp, blastx, tblastn. "
                    "Use auto unless the user explicitly asks for translated search or names a BLAST program. "
                    "Do not repeat the biological sequence and do not answer the user."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "request_without_full_sequence": _message_for_classifier(message),
                        "output_json_schema": {
                            "species": ["rice"],
                            "program": "auto",
                            "top_k": 5,
                            "evalue": 1e-10,
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


def run_blast_query(
    message: str,
    classification: dict[str, Any] | None = None,
    attachments: list[UploadedFileSummary] | None = None,
) -> dict[str, Any]:
    normalized = _coerce_classification(classification or _fallback_classification(message), message)
    try:
        queries = extract_query_sequences(message, attachments or [])
    except ValueError as exc:
        return _error_result(message, normalized, str(exc))
    if not queries:
        return _error_result(
            message,
            normalized,
            "No query sequence was recognized. Paste a DNA/RNA/protein sequence, a FASTA record, or upload a FASTA file.",
        )

    species_scope = normalized["species"] or list(BLAST_DATABASES)
    jobs: list[tuple[str, str, list[QuerySequence]]] = []
    not_found: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    grouped: dict[str, list[QuerySequence]] = {}
    for query in queries:
        program = _resolve_program(normalized["program"], query.sequence_type)
        if not program:
            not_found.append(
                {
                    "query_label": query.label,
                    "reason": f"Program {normalized['program']} is incompatible with {query.sequence_type} sequence.",
                }
            )
            continue
        grouped.setdefault(program, []).append(query)

    for program, program_queries in grouped.items():
        db_name = PROGRAMS[program]["db"]
        for species in species_scope:
            db_prefix = BLAST_DATABASES[species] / db_name
            if not _database_exists(db_prefix, db_name):
                errors.append({"species": species, "program": program, "error": f"BLAST database not found: {db_prefix}"})
                continue
            jobs.append((species, program, program_queries))

    all_rows: list[dict[str, Any]] = []
    if jobs:
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_JOBS, len(jobs))) as executor:
            future_map = {
                executor.submit(
                    _run_blast_command,
                    species,
                    program,
                    program_queries,
                    normalized["top_k"],
                    normalized["evalue"],
                ): (species, program)
                for species, program, program_queries in jobs
            }
            for future in as_completed(future_map):
                species, program = future_map[future]
                try:
                    all_rows.extend(future.result())
                except Exception as exc:
                    errors.append({"species": species, "program": program, "error": str(exc)})

    hits_by_query = _aggregate_hits(all_rows)
    matches = []
    for query in queries:
        hits = hits_by_query.get(query.query_id, [])
        if not hits:
            if not any(item.get("query_label") == query.label for item in not_found):
                not_found.append({"query_label": query.label, "reason": "No BLAST hit passed the configured e-value threshold."})
            continue
        ranked = sorted(hits, key=_hit_sort_key)
        for rank, hit in enumerate(ranked[: normalized["top_k"]], start=1):
            hit["rank"] = rank
        matches.append(
            {
                "query_label": query.label,
                "query_type": query.sequence_type,
                "query_length": len(query.sequence),
                "source": query.source,
                "total_hits": len(ranked),
                "returned_hits": min(len(ranked), normalized["top_k"]),
                "hits": ranked[: normalized["top_k"]],
            }
        )

    flat_hits = [hit for match in matches for hit in match["hits"]]
    gene_info_enrichment = enrich_blast_hits(flat_hits)

    return {
        "status": "completed",
        "analysis": "blast_query",
        "query": _query_summary(message, queries),
        "classification": normalized,
        "sequence_count": len(queries),
        "species_searched": species_scope,
        "matches": matches,
        "gene_info_enrichment": gene_info_enrichment,
        "not_found": not_found,
        "errors": errors,
    }


def extract_query_sequences(message: str, attachments: list[UploadedFileSummary]) -> list[QuerySequence]:
    records: list[tuple[str, str, str]] = []
    records.extend(_records_from_message(message))
    for attachment in attachments:
        if not attachment.path or Path(attachment.path).suffix.lower() not in FASTA_SUFFIXES:
            continue
        path = Path(attachment.path)
        if not path.is_file():
            continue
        records.extend(parse_fasta_text(path.read_text(encoding="utf-8-sig"), source=attachment.filename))
    if len(records) > MAX_QUERY_SEQUENCES:
        raise ValueError(f"At most {MAX_QUERY_SEQUENCES} FASTA sequences are supported per BLAST request.")

    queries = []
    seen: set[str] = set()
    for index, (label, raw_sequence, source) in enumerate(records, start=1):
        sequence = normalize_sequence(raw_sequence)
        if not sequence or sequence in seen:
            continue
        seen.add(sequence)
        sequence_type = detect_sequence_type(sequence)
        if sequence_type == "UNKNOWN":
            raise ValueError(f"Sequence {label} contains unsupported characters or its type cannot be determined.")
        if len(sequence) < MIN_SEQUENCE_LENGTH:
            raise ValueError(f"Sequence {label} is shorter than {MIN_SEQUENCE_LENGTH} residues.")
        if len(sequence) > MAX_SEQUENCE_LENGTH:
            raise ValueError(f"Sequence {label} exceeds the {MAX_SEQUENCE_LENGTH}-residue limit.")
        queries.append(
            QuerySequence(
                query_id=f"query_{index}",
                label=label or f"query_{index}",
                sequence=sequence.replace("U", "T") if sequence_type == "RNA" else sequence,
                sequence_type=sequence_type,
                source=source,
            )
        )
    return queries


def parse_fasta_text(raw: str, source: str = "pasted_fasta") -> list[tuple[str, str, str]]:
    if TRUNCATION_RE.search(raw or ""):
        raise ValueError("The sequence contains a truncation placeholder. Please provide the complete sequence.")
    records: list[tuple[str, str, str]] = []
    label = ""
    lines: list[str] = []
    for raw_line in (raw or "").splitlines():
        line = raw_line.strip()
        if line.startswith(">"):
            if label or lines:
                records.append((label or f"query_{len(records) + 1}", "".join(lines), source))
            label = line[1:].strip() or f"query_{len(records) + 1}"
            lines = []
            continue
        if label and line:
            lines.append(line)
    if label or lines:
        records.append((label or f"query_{len(records) + 1}", "".join(lines), source))
    return records


def normalize_sequence(raw: str) -> str:
    return re.sub(r"[^A-Za-z*]+", "", str(raw or "")).upper()


def detect_sequence_type(sequence: str) -> str:
    symbols = set(sequence.upper())
    if not symbols:
        return "UNKNOWN"
    if "U" in symbols and "T" in symbols and symbols <= DNA_IUPAC | RNA_IUPAC:
        return "UNKNOWN"
    if symbols <= DNA_IUPAC:
        return "DNA"
    if symbols <= RNA_IUPAC:
        return "RNA"
    if symbols <= PROTEIN_IUPAC:
        return "PROTEIN"
    return "UNKNOWN"


def _records_from_message(message: str) -> list[tuple[str, str, str]]:
    if re.search(r"(?m)^\s*>", message or ""):
        return parse_fasta_text(message, source="message_fasta")
    candidates = []
    wrapped_lines: list[str] = []
    for line in (message or "").splitlines():
        compact = re.sub(r"\s+", "", line)
        if (
            len(compact) >= 20
            and re.fullmatch(r"[A-Za-z*]+", compact)
            and detect_sequence_type(normalize_sequence(compact)) != "UNKNOWN"
        ):
            wrapped_lines.append(compact)
        elif wrapped_lines:
            candidates.append("".join(wrapped_lines))
            wrapped_lines = []
    if wrapped_lines:
        candidates.append("".join(wrapped_lines))
    if not candidates:
        candidates = SEQUENCE_TOKEN_RE.findall(message or "")
    if not candidates:
        raw = (message or "").strip()
        if not re.search(r"\s", raw):
            compact = re.sub(r"\s+", "", raw)
            if len(compact) >= MIN_SEQUENCE_LENGTH and re.fullmatch(r"[A-Za-z*]+", compact):
                candidates = [compact]
    return [(f"query_{index}", sequence, "message") for index, sequence in enumerate(candidates, start=1)]


def _run_blast_command(
    species: str,
    program: str,
    queries: list[QuerySequence],
    top_k: int,
    evalue: float,
) -> list[dict[str, Any]]:
    executable = shutil.which(program)
    if not executable:
        raise RuntimeError(f"{program} executable is not available in PATH.")
    db_prefix = BLAST_DATABASES[species] / PROGRAMS[program]["db"]
    query_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".fa", encoding="utf-8", delete=False) as handle:
            query_path = handle.name
            for query in queries:
                handle.write(f">{query.query_id}\n{query.sequence}\n")
        command = [
            executable,
            "-query",
            query_path,
            "-db",
            str(db_prefix),
            "-outfmt",
            OUTFMT,
            "-max_target_seqs",
            str(max(5, top_k)),
            "-evalue",
            str(evalue),
            "-num_threads",
            str(BLAST_THREADS),
        ]
        if program == "blastn" and all(len(item.sequence) < 50 for item in queries):
            command.extend(["-task", "blastn-short", "-word_size", "7"])
        process = subprocess.run(command, capture_output=True, text=True, timeout=BLAST_TIMEOUT_SECONDS)
        if process.returncode:
            raise RuntimeError((process.stderr or "BLAST execution failed.").strip()[:2000])
        return _parse_tabular_output(process.stdout, species, program)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{program} timed out after {BLAST_TIMEOUT_SECONDS} seconds.") from exc
    finally:
        if query_path:
            Path(query_path).unlink(missing_ok=True)


def _parse_tabular_output(output: str, species: str, program: str) -> list[dict[str, Any]]:
    rows = []
    for values in csv.reader((output or "").splitlines(), delimiter="\t"):
        if len(values) != len(OUTFMT_COLUMNS):
            continue
        item = dict(zip(OUTFMT_COLUMNS, values))
        rows.append(
            {
                "query_id": item["qseqid"],
                "species": species,
                "species_label": SPECIES_LABELS[species],
                "program": program,
                "record_type": PROGRAMS[program]["record_type"],
                "subject_id": item["sseqid"],
                "description": "" if item["stitle"] == "N/A" else item["stitle"],
                "alignment_length": int(item["length"]),
                "identities": int(item["nident"]),
                "mismatches": int(item["mismatch"]),
                "gaps": int(item["gaps"]),
                "query_start": int(item["qstart"]),
                "query_end": int(item["qend"]),
                "subject_start": int(item["sstart"]),
                "subject_end": int(item["send"]),
                "evalue": float(item["evalue"]),
                "bitscore": float(item["bitscore"]),
                "query_length": int(item["qlen"]),
                "subject_length": int(item["slen"]),
            }
        )
    return rows


def _aggregate_hits(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["query_id"], row["species"], row["program"], row["subject_id"]), []).append(row)
    by_query: dict[str, list[dict[str, Any]]] = {}
    for (query_id, species, program, subject_id), segments in grouped.items():
        best = min(segments, key=lambda item: (item["evalue"], -item["bitscore"]))
        query_coverage = _coverage([(item["query_start"], item["query_end"]) for item in segments], best["query_length"])
        subject_coverage = _coverage([(item["subject_start"], item["subject_end"]) for item in segments], best["subject_length"])
        total_alignment = sum(item["alignment_length"] for item in segments)
        total_identities = sum(item["identities"] for item in segments)
        hit = {
            "species": species,
            "species_label": SPECIES_LABELS[species],
            "program": program,
            "database": PROGRAMS[program]["db"],
            "record_type": PROGRAMS[program]["record_type"],
            "subject_id": subject_id,
            "description": best["description"],
            "best_evalue": best["evalue"],
            "best_bitscore": round(best["bitscore"], 3),
            "identity": round(100 * total_identities / total_alignment, 2) if total_alignment else 0.0,
            "query_coverage": query_coverage,
            "subject_coverage": subject_coverage,
            "hsp_count": len(segments),
            "alignment_length": total_alignment,
            "identities": total_identities,
            "gaps": sum(item["gaps"] for item in segments),
            "segments": [
                {
                    key: item[key]
                    for key in (
                        "query_start",
                        "query_end",
                        "subject_start",
                        "subject_end",
                        "evalue",
                        "bitscore",
                        "alignment_length",
                    )
                }
                for item in segments[:5]
            ],
        }
        by_query.setdefault(query_id, []).append(hit)
    return by_query


def _coverage(intervals: list[tuple[int, int]], total_length: int) -> float:
    if not intervals or not total_length:
        return 0.0
    normalized = sorted((min(start, end), max(start, end)) for start, end in intervals)
    merged = [normalized[0]]
    for start, end in normalized[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end + 1:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return round(100 * sum(end - start + 1 for start, end in merged) / total_length, 2)


def _hit_sort_key(hit: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(hit["best_evalue"]),
        -float(hit["best_bitscore"]),
        -float(hit["identity"]),
        -float(hit["query_coverage"]),
    )


def _resolve_program(program: str, sequence_type: str) -> str | None:
    if program == "auto":
        return "blastp" if sequence_type == "PROTEIN" else "blastn"
    if sequence_type in PROGRAMS[program]["query_types"]:
        return program
    return None


def _database_exists(prefix: Path, db_name: str) -> bool:
    suffixes = (".nsq", ".nin", ".nhr") if db_name == "nt" else (".psq", ".pin", ".phr")
    return any(Path(str(prefix) + suffix).is_file() for suffix in suffixes)


def _fallback_classification(message: str) -> dict[str, Any]:
    return _coerce_classification(
        {
            "species": _species_scope(message),
            "program": _program_from_message(message),
            "top_k": _extract_top_k(message),
            "evalue": _extract_evalue(message),
            "reason": "fallback parser",
        },
        message,
    )


def _coerce_classification(payload: dict[str, Any], message: str) -> dict[str, Any]:
    species = [_normalize_species(item) for item in _string_list(payload.get("species"))]
    species = _dedupe([item for item in species if item in BLAST_DATABASES])
    program = str(payload.get("program") or "auto").strip().lower()
    if program not in {*PROGRAMS, "auto"}:
        program = _program_from_message(message)
    return {
        "species": species,
        "program": program,
        "top_k": _clamp_top_k(payload.get("top_k")),
        "evalue": _clamp_evalue(payload.get("evalue")),
        "reason": str(payload.get("reason") or "").strip(),
    }


def _species_scope(message: str) -> list[str]:
    found = []
    patterns = {
        "ath": r"arabidopsis|拟南芥|\bath\b|tair",
        "maize": r"maize|corn|玉米|zea",
        "rice": r"rice|水稻|oryza",
        "soy": r"soybean|soy|大豆|glycine",
    }
    for species, pattern in patterns.items():
        if re.search(pattern, message or "", re.I):
            found.append(species)
    return found


def _normalize_species(value: str) -> str:
    text = str(value).strip().lower()
    aliases = {
        "arabidopsis": "ath",
        "arabidopsis thaliana": "ath",
        "拟南芥": "ath",
        "maize": "maize",
        "corn": "maize",
        "玉米": "maize",
        "rice": "rice",
        "水稻": "rice",
        "soy": "soy",
        "soybean": "soy",
        "大豆": "soy",
    }
    return aliases.get(text, text)


def _program_from_message(message: str) -> str:
    for program in ("tblastn", "blastx", "blastp", "blastn"):
        if re.search(rf"\b{program}\b", message or "", re.I):
            return program
    return "auto"


def _extract_top_k(message: str) -> int:
    match = re.search(r"\btop\s*[-_ ]?\s*(\d{1,2})\b", message or "", re.I)
    return _clamp_top_k(match.group(1) if match else DEFAULT_TOP_K)


def _extract_evalue(message: str) -> float:
    match = re.search(r"(?:e[-_ ]?value|e值)\s*[=:：]?\s*(\d+(?:\.\d+)?e[+-]?\d+|\d+(?:\.\d+)?)", message or "", re.I)
    return _clamp_evalue(match.group(1) if match else DEFAULT_EVALUE)


def _clamp_top_k(value: Any) -> int:
    try:
        return max(1, min(MAX_TOP_K, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_TOP_K


def _clamp_evalue(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_EVALUE
    return number if 0 < number <= 10 else DEFAULT_EVALUE


def _message_for_classifier(message: str) -> str:
    text = re.sub(r"(?ms)^\s*>.*?(?=^\s*>|\Z)", "[FASTA sequence omitted]", message or "")
    return SEQUENCE_TOKEN_RE.sub("[sequence omitted]", text)[:2000]


def _query_summary(message: str, queries: list[QuerySequence]) -> str:
    labels = ", ".join(item.label for item in queries)
    return f"BLAST request with {len(queries)} sequence(s): {labels}. Original instruction: {_message_for_classifier(message)[:500]}"


def _error_result(message: str, classification: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "status": "need_user_input",
        "analysis": "blast_query",
        "query": _message_for_classifier(message),
        "classification": classification,
        "sequence_count": 0,
        "species_searched": classification.get("species") or list(BLAST_DATABASES),
        "matches": [],
        "not_found": [],
        "errors": [{"error": error}],
    }


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _dedupe(items: list[str]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _json_from_text(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", str(text or "").strip(), re.S)
    payload = json.loads(match.group(0) if match else text)
    if not isinstance(payload, dict):
        raise ValueError("BLAST classifier response must be a JSON object.")
    return payload

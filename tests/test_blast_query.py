import asyncio
import json
import shutil
from pathlib import Path

import pytest

from backend.app.schemas import UploadedFileSummary
from backend.app.services import blast_query
from backend.app.services.blast_query import QuerySequence, classify_blast_query, extract_query_sequences, run_blast_query
from backend.app.services.code_executor import execute_skill
from backend.app.services.skill_loader import load_skill


class FakeSettings:
    router_model = "fake-router"


class SoyProteinLLM:
    available = True
    settings = FakeSettings()

    def __init__(self) -> None:
        self.kwargs = {}

    async def chat(self, messages, **kwargs):
        self.kwargs = kwargs
        return json.dumps(
            {
                "species": ["soy"],
                "program": "blastp",
                "top_k": 2,
                "evalue": 1e-10,
                "reason": "soybean protein BLAST requested",
            }
        )


def fake_rows(species: str, program: str, queries: list[QuerySequence], _top_k: int, _evalue: float) -> list[dict]:
    return [
        {
            "query_id": query.query_id,
            "species": species,
            "species_label": blast_query.SPECIES_LABELS[species],
            "program": program,
            "record_type": blast_query.PROGRAMS[program]["record_type"],
            "subject_id": f"{species}_{query.query_id}",
            "description": "",
            "alignment_length": len(query.sequence),
            "identities": len(query.sequence),
            "mismatches": 0,
            "gaps": 0,
            "query_start": 1,
            "query_end": len(query.sequence),
            "subject_start": 1,
            "subject_end": len(query.sequence),
            "evalue": 1e-40,
            "bitscore": 200.0,
            "query_length": len(query.sequence),
            "subject_length": len(query.sequence),
        }
        for query in queries
    ]


def test_classify_blast_query_uses_json_output() -> None:
    llm = SoyProteinLLM()

    classification = asyncio.run(classify_blast_query("Run soybean blastp for this protein", llm))

    assert llm.kwargs["response_format"] == {"type": "json_object"}
    assert classification["species"] == ["soy"]
    assert classification["program"] == "blastp"
    assert classification["top_k"] == 2


def test_extract_multi_fasta_preserves_labels() -> None:
    queries = extract_query_sequences(">alpha\nACGTACGTACGTACGTACGT\n>beta\nACGTACGTACGTACGTACGA", [])

    assert [query.label for query in queries] == ["alpha", "beta"]
    assert [query.sequence_type for query in queries] == ["DNA", "DNA"]


def test_extract_wrapped_bare_sequence_stays_one_query() -> None:
    queries = extract_query_sequences("ACGTACGTACGTACGTACGT\nACGTACGTACGTACGTACGA", [])

    assert len(queries) == 1
    assert len(queries[0].sequence) == 40


def test_run_blast_query_batches_multi_fasta_by_species_not_sequence(monkeypatch) -> None:
    calls = []

    def record_batch(species, program, queries, top_k, evalue):
        calls.append((species, program, [query.label for query in queries]))
        return fake_rows(species, program, queries, top_k, evalue)

    monkeypatch.setattr(blast_query, "_database_exists", lambda *_args: True)
    monkeypatch.setattr(blast_query, "_run_blast_command", record_batch)

    result = run_blast_query(
        ">alpha\nACGTACGTACGTACGTACGT\n>beta\nACGTACGTACGTACGTACGA",
        {"species": ["rice", "soy"], "program": "auto", "top_k": 5, "evalue": 1e-10},
    )

    assert result["status"] == "completed"
    assert result["sequence_count"] == 2
    assert len(calls) == 2
    assert sorted(species for species, _program, _labels in calls) == ["rice", "soy"]
    assert all(labels == ["alpha", "beta"] for _species, _program, labels in calls)


def test_run_blast_query_uses_uploaded_fasta(tmp_path, monkeypatch) -> None:
    fasta = tmp_path / "query.faa"
    fasta.write_text(">protein-a\nMKTIIALSYIFCLVFADYKDDDDK\n", encoding="utf-8")
    attachment = UploadedFileSummary(
        file_id="protein",
        filename=fasta.name,
        content_type="text/plain",
        size=fasta.stat().st_size,
        path=str(fasta),
    )
    monkeypatch.setattr(blast_query, "_database_exists", lambda *_args: True)
    monkeypatch.setattr(blast_query, "_run_blast_command", fake_rows)

    result = run_blast_query(
        "Run soybean BLAST for the uploaded FASTA.",
        {"species": ["soy"], "program": "auto", "top_k": 5, "evalue": 1e-10},
        [attachment],
    )

    assert result["sequence_count"] == 1
    assert result["matches"][0]["query_label"] == "protein-a"
    assert result["matches"][0]["hits"][0]["record_type"] == "protein_record"


def test_blast_query_rejects_more_than_ten_sequences() -> None:
    fasta = "\n".join(f">query-{index}\nACGTACGTACGTACGTACGT" for index in range(11))

    result = run_blast_query(fasta, {"species": ["rice"], "program": "auto", "top_k": 5, "evalue": 1e-10})

    assert result["status"] == "need_user_input"
    assert "At most 10" in result["errors"][0]["error"]


def test_blast_query_registered_skill_executes(monkeypatch) -> None:
    monkeypatch.setattr(blast_query, "_database_exists", lambda *_args: True)
    monkeypatch.setattr(blast_query, "_run_blast_command", fake_rows)
    skill = load_skill(Path("skill/blast_query.md"))

    output = asyncio.run(execute_skill(">protein-a\nMKTIIALSYIFCLVFADYKDDDDK", skill, SoyProteinLLM()))

    assert output["mode"] == "deterministic_query"
    assert output["result"]["analysis"] == "blast_query"
    assert output["result"]["matches"][0]["hits"][0]["subject_id"] == "soy_query_1"


def test_real_soybean_protein_blast_when_local_database_exists() -> None:
    fasta = Path("data/blast_db/Soybean/test.fa")
    database_parts = list(Path("data/blast_db/Soybean").glob("protein*.pin"))
    if not fasta.is_file() or not database_parts or not shutil.which("blastp"):
        pytest.skip("Local soybean protein BLAST fixture is unavailable.")
    attachment = UploadedFileSummary(
        file_id="real-soy",
        filename=fasta.name,
        content_type="text/plain",
        size=fasta.stat().st_size,
        path=str(fasta.resolve()),
    )

    result = run_blast_query(
        "Run soybean blastp for uploaded FASTA.",
        {"species": ["soy"], "program": "blastp", "top_k": 3, "evalue": 1e-10},
        [attachment],
    )

    assert result["status"] == "completed"
    assert result["matches"]
    assert result["matches"][0]["hits"][0]["subject_id"] == "GmW82.01G000100.1"
    assert result["matches"][0]["hits"][0]["gene_info"]["canonical_id"] == "Glyma.01G000100"
    assert result["matches"][0]["hits"][0]["gene_info"]["matched"] is True

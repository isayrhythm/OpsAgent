from pathlib import Path

from backend.app.schemas import UploadedFileSummary
from backend.app.services.data_intake import intake_uploaded_file, pdf_context_for_history, profile_uploaded_files


def write_text_pdf(path: Path, text: str) -> None:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        b"5 0 obj\n<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream\nendobj\n",
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(content))
        content.extend(obj)
    xref = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    path.write_bytes(content)


def test_profiles_proteomics_expression_matrix_fixture() -> None:
    matches = list(Path("data").glob("40430*/input/Leaf.report.pg_matrix.tsv"))
    if not matches:
        return
    path = matches[0].resolve()
    attachment = UploadedFileSummary(
        file_id="fixture",
        filename=path.name,
        content_type="text/tab-separated-values",
        size=path.stat().st_size,
        path=str(path),
    )

    profile = profile_uploaded_files([attachment])[0]

    assert profile["status"] == "profiled"
    assert profile["data_family"] == "proteomics"
    assert profile["data_type"] == "expression_matrix"
    assert profile["recommended_skills"] == ["differential_protein_analysis"]
    assert profile["sample_groups"]["WT"] == ["WT1-Y", "WT2-Y", "WT3-Y"]
    assert profile["sample_groups"]["MT"] == ["MT1-Y", "MT2-Y", "MT3-Y"]


def test_intake_retries_sparse_matrix_until_standard_matrix_is_ready(tmp_path) -> None:
    source = tmp_path / "sparse_proteomics.csv"
    source.write_text(
        "\n".join(
            [
                "Protein.Names,Genes,First.Protein.Description,WT1,WT2,MT1,MT2",
                "P1,G1,protein one,10,,20,",
                "P2,G2,protein two,,11,,21",
                "P3,G3,protein three,9,,19,",
                "P4,G4,protein four,,12,,22",
            ]
        ),
        encoding="utf-8",
    )
    attachment = UploadedFileSummary(
        file_id="sparse",
        filename=source.name,
        content_type="text/csv",
        size=source.stat().st_size,
        path=str(source),
    )

    intake = intake_uploaded_file(attachment)

    assert intake["status"] == "ready"
    assert [attempt["status"] for attempt in intake["attempts"]] == ["failed", "completed"]
    assert Path(intake["standard_files"]["matrix"]).is_file()
    assert Path(intake["standard_files"]["sample_metadata"]).is_file()


def test_intake_preserves_blank_header_gene_id_column_for_counts_matrix(tmp_path) -> None:
    source = tmp_path / "rice_counts.tsv"
    source.write_text(
        "\n".join(
            [
                "\tMT-D1\tMT-D2\tWT-D1\tWT-D2",
                "AGIS_Os01g000010\t3591\t3659\t3444\t2936",
                "AGIS_Os01g000020\t22\t22\t8\t10",
                "AGIS_Os01g000030\t400\t360\t416\t323",
            ]
        ),
        encoding="utf-8",
    )
    attachment = UploadedFileSummary(
        file_id="rice-counts",
        filename=source.name,
        content_type="text/tab-separated-values",
        size=source.stat().st_size,
        path=str(source),
    )

    intake = intake_uploaded_file(attachment)
    matrix_text = Path(intake["standard_files"]["matrix"]).read_text(encoding="utf-8")

    assert intake["status"] == "ready"
    assert intake["data_family"] == "transcriptomics"
    assert intake["recommended_skills"] == ["differential_transcriptomics_analysis"]
    assert intake["sample_groups"] == {"MT-D": ["MT-D1", "MT-D2"], "WT-D": ["WT-D1", "WT-D2"]}
    assert intake["feature_count"] == 3
    assert "AGIS_Os01g000010" in matrix_text


def test_trait_rank_table_stays_low_confidence_without_analysis_skill(tmp_path) -> None:
    source = tmp_path / "ENSR_soybean_rank.csv"
    source.write_text(
        "\n".join(
            [
                "feature_id,100seed_weight,affect_auxins_homeostasis,affect_jasmonates_homeostasis,leaf_area,cold_tolerance",
                "Glyma.01G000100,0.9,0.2,0.3,0.7,0.5",
                "Glyma.01G000200,0.8,0.4,0.5,0.6,0.3",
                "Glyma.01G000300,0.7,0.1,0.6,0.5,0.4",
                "Glyma.01G000400,0.6,0.3,0.7,0.4,0.2",
            ]
        ),
        encoding="utf-8",
    )
    attachment = UploadedFileSummary(
        file_id="rank",
        filename=source.name,
        content_type="text/csv",
        size=source.stat().st_size,
        path=str(source),
    )

    intake = intake_uploaded_file(attachment)

    assert intake["status"] == "profiled"
    assert intake["data_type"] == "expression_matrix"
    assert intake["confidence"] == "low"
    assert intake["analysis_ready"] is False
    assert intake["recommended_skills"] == []
    assert intake["capabilities"] == []
    assert "standard_files" not in intake
    assert any("性状" in warning or "复样" in warning for warning in intake["warnings"])


def test_pdf_intake_extracts_text_and_context(tmp_path) -> None:
    source = tmp_path / "paper.pdf"
    write_text_pdf(source, "Arabidopsis HY2 regulates photomorphogenesis and seedling development.")
    attachment = UploadedFileSummary(
        file_id="paper",
        filename=source.name,
        content_type="application/pdf",
        size=source.stat().st_size,
        path=str(source),
    )

    intake = intake_uploaded_file(attachment)
    ready_attachment = attachment.model_copy(update={"intake": intake})
    context = pdf_context_for_history([ready_attachment])

    assert intake["status"] == "ready"
    assert intake["data_family"] == "literature"
    assert intake["data_type"] == "pdf_document"
    assert intake["capabilities"] == ["pdf_literature_context"]
    assert Path(intake["text_file"]).is_file()
    assert "HY2 regulates photomorphogenesis" in intake["text_excerpt"]
    assert "PDF 文献上下文" in context
    assert "paper.pdf" in context


def test_pdf_intake_reports_unextractable_pdf(tmp_path) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF-1.4\nnot a readable pdf")
    attachment = UploadedFileSummary(
        file_id="scan",
        filename=source.name,
        content_type="application/pdf",
        size=source.stat().st_size,
        path=str(source),
    )

    intake = intake_uploaded_file(attachment)

    assert intake["status"] == "failed"
    assert intake["data_type"] == "pdf_document"
    assert "PDF" in intake["reason"]

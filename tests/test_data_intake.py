from pathlib import Path

from backend.app.schemas import UploadedFileSummary
from backend.app.services.data_intake import intake_uploaded_file, profile_uploaded_files


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

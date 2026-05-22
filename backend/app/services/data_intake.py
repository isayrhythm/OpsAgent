from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd

from backend.app.schemas import UploadedFileSummary


SUPPORTED_TABLE_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xls", ".xlsm"}
DEFAULT_MAX_INTAKE_ATTEMPTS = 3
INTAKE_VERSION = 4


def ensure_attachment_intakes(attachments: list[UploadedFileSummary]) -> list[UploadedFileSummary]:
    ready: list[UploadedFileSummary] = []
    for item in attachments:
        if (
            item.intake
            and item.intake.get("status") == "ready"
            and item.intake.get("intake_version") == INTAKE_VERSION
        ):
            ready.append(item)
            continue
        ready.append(item.model_copy(update={"intake": intake_uploaded_file(item)}))
    return ready


def intake_uploaded_file(
    item: UploadedFileSummary,
    max_attempts: int = DEFAULT_MAX_INTAKE_ATTEMPTS,
) -> dict[str, Any]:
    if not item.path:
        return _failed_intake(item, "上传文件缺少保存路径。")
    path = Path(item.path)
    if path.suffix.lower() not in SUPPORTED_TABLE_SUFFIXES:
        return {
            "status": "skipped",
            "data_family": "unknown",
            "data_type": "unsupported_file",
            "confidence": "unconfirmed",
            "analysis_ready": False,
            "reason": "当前 intake 只处理 CSV/TSV/TXT/XLSX 表格。",
            "warnings": [],
            "capabilities": [],
        }

    profile = _profile_file(item, path)
    if (
        profile["status"] != "profiled"
        or profile["data_type"] != "expression_matrix"
        or profile.get("confidence") != "high"
    ):
        return profile

    try:
        frame = read_table(path)
    except Exception as exc:
        return {**profile, "status": "failed", "reason": f"读取原始文件失败：{exc}", "capabilities": []}

    attempts: list[dict[str, Any]] = []
    intake_dir = path.parent / f"{item.file_id}_intake"
    intake_dir.mkdir(parents=True, exist_ok=True)
    for index, strategy in enumerate(_expression_matrix_strategies()[:max_attempts], start=1):
        try:
            standard = _standardize_expression_matrix(frame, intake_dir, str(profile["data_family"]), strategy)
            attempts.append(
                {
                    "attempt": index,
                    "strategy": strategy["id"],
                    "status": "completed",
                    "sample_count": standard["sample_count"],
                    "feature_count": standard["feature_count"],
                }
            )
            profile.update(
                {
                    "status": "ready",
                    "intake_version": INTAKE_VERSION,
                    "feature_count": standard["feature_count"],
                    "sample_count": standard["sample_count"],
                    "sample_groups": standard["sample_groups"],
                    "numeric_sample_columns": standard["sample_columns"][:40],
                    "standard_files": standard["standard_files"],
                    "capabilities": capabilities_for_profile(profile),
                    "attempts": attempts,
                    "max_attempts": max_attempts,
                }
            )
            (intake_dir / "data_intake_output.json").write_text(
                json.dumps(profile, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return profile
        except Exception as exc:
            attempts.append(
                {
                    "attempt": index,
                    "strategy": strategy["id"],
                    "status": "failed",
                    "reason": str(exc),
                }
            )

    return {
        **profile,
        "status": "failed",
        "analysis_ready": False,
        "reason": f"{max_attempts} 次 intake 后仍未得到可用于 R 分析的标准矩阵。",
        "attempts": attempts,
        "max_attempts": max_attempts,
        "recommended_skills": [],
        "capabilities": [],
    }


def profile_uploaded_files(attachments: list[UploadedFileSummary]) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for item in attachments:
        if item.intake:
            profiles.append(prompt_profile(item, item.intake))
            continue
        if item.path and Path(item.path).suffix.lower() in SUPPORTED_TABLE_SUFFIXES:
            profiles.append(prompt_profile(item, _profile_file(item, Path(item.path))))
    return profiles


def prompt_profile(item: UploadedFileSummary, intake: dict[str, Any]) -> dict[str, Any]:
    profile = {
        "file_id": item.file_id,
        "filename": item.filename,
        "content_type": item.content_type,
        "size": item.size,
        "path": item.path,
        "status": intake.get("status", "unknown"),
        "data_family": intake.get("data_family", "unknown"),
        "data_type": intake.get("data_type", "unknown_table"),
        "confidence": intake.get("confidence", "unconfirmed"),
        "analysis_ready": bool(intake.get("analysis_ready")),
        "reason": intake.get("reason", ""),
        "warnings": intake.get("warnings", []),
        "columns_preview": intake.get("columns_preview", []),
        "sample_groups": intake.get("sample_groups", {}),
        "feature_count": intake.get("feature_count"),
        "sample_count": intake.get("sample_count"),
        "recommended_skills": intake.get("recommended_skills", []),
        "capabilities": intake.get("capabilities", []),
        "standard_files": intake.get("standard_files", {}),
    }
    return {key: value for key, value in profile.items() if value not in (None, "", [], {})}


def read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        return _clean_frame(pd.read_excel(path))
    return _read_delimited_table(path, max_rows=None)


def read_preview_table(path: Path, max_rows: int = 2000) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        return _clean_frame(pd.read_excel(path, nrows=max_rows))
    return _read_delimited_table(path, max_rows=max_rows)


def _read_delimited_table(path: Path, max_rows: int | None) -> pd.DataFrame:
    best: pd.DataFrame | None = None
    best_score = -1
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        for skiprows in range(0, 20):
            try:
                frame = pd.read_csv(
                    path,
                    sep=None,
                    engine="python",
                    encoding=encoding,
                    skiprows=skiprows,
                    nrows=max_rows,
                )
            except Exception:
                continue
            frame = _clean_frame(frame)
            score = len(detect_numeric_sample_columns(frame)) * 10 + min(len(frame.columns), 30)
            if score > best_score:
                best = frame
                best_score = score
            if score >= 50:
                return frame
    if best is None:
        raise ValueError("无法读取表格预览")
    return best


def detect_numeric_sample_columns(frame: pd.DataFrame, min_numeric_ratio: float = 0.55) -> list[str]:
    sample_columns: list[str] = []
    for column in frame.columns:
        name = str(column).strip()
        lowered = name.lower()
        if _looks_like_annotation_column(lowered):
            continue
        values = pd.to_numeric(
            frame[column].astype(str).str.replace(",", "", regex=False).str.strip(),
            errors="coerce",
        )
        non_null = values.notna().sum()
        if non_null >= 2 and non_null / max(len(frame), 1) >= min_numeric_ratio:
            sample_columns.append(name)
    return sample_columns


def infer_sample_groups(sample_columns: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for sample in sample_columns:
        label = sample_group_label(sample)
        groups.setdefault(label, []).append(sample)
    return {label: samples for label, samples in groups.items() if len(samples) >= 2}


def sample_group_label(sample: str) -> str:
    value = sample.strip()
    match = re.match(r"^(.+?)[_\-. ]?\d+", value)
    if match:
        return match.group(1).strip("_-. ")
    match = re.match(r"^([A-Za-z]+)", value)
    if match:
        return match.group(1)
    return re.split(r"[_\-. ]", value)[0]


def detect_data_type(frame: pd.DataFrame) -> str:
    columns = {str(column).strip().lower() for column in frame.columns}
    if columns.intersection({"pvalue", "p.value", "padj", "fdr", "log2fc", "log2_fc", "fold_change", "regulation"}):
        return "diff_result"
    if len(detect_numeric_sample_columns(frame)) >= 4:
        return "expression_matrix"
    if len(detect_numeric_sample_columns(frame, min_numeric_ratio=0.2)) >= 4:
        return "expression_matrix"
    return "unknown_table"


def detect_data_family(frame: pd.DataFrame, data_type: str) -> str:
    normalized_columns = {_normalized_column_name(column) for column in frame.columns}
    proteomics_columns = {
        "protein_names",
        "protein_group",
        "protein_ids",
        "proteotypic",
        "peptide",
        "peptide_id",
        "n_sequences",
        "first_protein_description",
    }
    transcriptomics_columns = {
        "gene_id",
        "gene_short_name",
        "transcript",
        "transcript_id",
        "biotype",
        "ensembl",
        "locus",
        "length",
        "tpm",
        "fpkm",
    }
    if normalized_columns.intersection(proteomics_columns) or any(
        column.startswith("protein_") for column in normalized_columns
    ):
        return "proteomics"
    if normalized_columns.intersection(transcriptomics_columns):
        return "transcriptomics"
    feature_id_column = _first_matching_column(frame, ("gene_id", "locus", "feature_id", "id"))
    if data_type == "expression_matrix" and feature_id_column and _looks_like_gene_ids(frame[feature_id_column]):
        return "transcriptomics"
    if data_type == "expression_matrix":
        return "expression"
    return "unknown"


def recommend_skills(data_family: str, data_type: str, confidence: str = "unconfirmed") -> list[str]:
    if confidence != "high":
        return []
    if data_family == "proteomics" and data_type == "expression_matrix":
        return ["differential_protein_analysis"]
    if data_family == "transcriptomics" and data_type == "expression_matrix":
        return ["differential_transcriptomics_analysis"]
    return []


def capabilities_for_profile(profile: dict[str, Any]) -> list[str]:
    if profile.get("confidence") != "high" and not profile.get("analysis_ready"):
        return []
    if profile.get("data_family") == "proteomics" and profile.get("data_type") == "expression_matrix":
        return ["differential_protein_analysis", "volcano_report", "heatmap_report"]
    if profile.get("data_family") == "transcriptomics" and profile.get("data_type") == "expression_matrix":
        return ["differential_transcriptomics_analysis", "volcano_report", "heatmap_report"]
    if profile.get("data_type") == "expression_matrix":
        return ["expression_matrix_ready"]
    return []


def uploaded_files_prompt(attachments: list[UploadedFileSummary]) -> str:
    if not attachments:
        return "当前会话没有上传文件。"
    sections = ["当前会话已上传文件。以下是 intake 后的短摘要，不包含原始文件内容："]
    for item in attachments:
        intake = item.intake or {}
        groups = intake.get("sample_groups") or {}
        group_text = ", ".join(f"{group}({len(samples)})" for group, samples in groups.items()) or "未识别"
        columns = ", ".join(str(column) for column in (intake.get("columns_preview") or [])[:12]) or "未识别"
        capabilities = ", ".join(intake.get("capabilities") or []) or "待确认"
        warnings = "；".join(str(warning) for warning in (intake.get("warnings") or [])) or "无"
        standard_files = intake.get("standard_files") or {}
        sections.append(
            "\n".join(
                [
                    f"- 文件名：{item.filename}",
                    f"  文件位置：{item.path or 'unknown'}",
                    f"  文件大小：{item.size} bytes",
                    f"  intake 状态：{intake.get('status', 'not_processed')}",
                    f"  数据识别：{intake.get('data_family', 'unknown')}/{intake.get('data_type', 'unknown_table')}",
                    f"  识别置信度：{intake.get('confidence', 'unconfirmed')}",
                    f"  识别警告：{warnings}",
                    f"  标准矩阵规模：features={intake.get('feature_count', 'unknown')}; samples={intake.get('sample_count', 'unknown')}",
                    f"  原始结构预览：{columns}",
                    f"  标准化结构：matrix={standard_files.get('matrix', 'not_ready')}; metadata={standard_files.get('sample_metadata', 'not_ready')}",
                    f"  已识别分组：{group_text}",
                    f"  可用于：{capabilities}",
                ]
            )
        )
    return "\n".join(sections)


def build_standard_matrix(frame: pd.DataFrame, sample_columns: list[str], data_family: str) -> pd.DataFrame:
    family_candidates = {
        "proteomics": {
            "id": ("Protein.Names", "Protein.Group", "Protein.IDs", "protein", "Genes", "gene", "feature_id", "id"),
            "name": ("Genes", "gene", "Protein.Names", "protein", "name"),
            "description": ("First.Protein.Description", "description", "annotation"),
        },
        "transcriptomics": {
            "id": ("gene_id", "gene", "locus", "transcript", "feature_id", "id"),
            "name": ("gene_short_name", "gene_name", "gene", "symbol", "name"),
            "description": ("description", "annotation", "biotype"),
        },
    }
    candidates = family_candidates.get(
        data_family,
        {
            "id": ("feature_id", "id", "gene", "protein", "name"),
            "name": ("name", "gene", "protein", "feature_id", "id"),
            "description": ("description", "annotation"),
        },
    )
    feature_id_col = _first_matching_column(frame, candidates["id"])
    feature_name_col = _first_matching_column(frame, candidates["name"])
    description_col = _first_matching_column(frame, candidates["description"])

    matrix = pd.DataFrame()
    matrix["feature_id"] = (
        frame[feature_id_col].astype(str).str.strip()
        if feature_id_col
        else pd.Series([f"feature_{index + 1}" for index in range(len(frame))])
    )
    matrix["feature_name"] = (
        frame[feature_name_col].astype(str).str.strip()
        if feature_name_col
        else matrix["feature_id"]
    )
    matrix["description"] = (
        frame[description_col].astype(str).str.strip()
        if description_col
        else ""
    )
    for column in sample_columns:
        matrix[column] = _numeric_series(frame[column])
    matrix = matrix.dropna(subset=sample_columns, how="all")
    matrix = matrix[matrix["feature_id"].astype(str).str.len() > 0]
    return matrix.reset_index(drop=True)


def explain_profile(data_family: str, data_type: str, frame: pd.DataFrame, sample_columns: list[str]) -> str:
    return (
        f"识别为 {data_family}/{data_type}；"
        f"预览 {len(frame)} 行、{len(frame.columns)} 列；"
        f"检测到 {len(sample_columns)} 个数值样本列。"
    )


def profile_confidence(
    data_family: str,
    data_type: str,
    sample_columns: list[str],
    groups: dict[str, list[str]],
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if data_type != "expression_matrix":
        return "unconfirmed", warnings
    if data_family not in {"proteomics", "transcriptomics"}:
        warnings.append("缺少可确认组学类型的列证据。")
    replicate_columns = [column for column in sample_columns if _looks_like_replicate_sample(column)]
    if len(replicate_columns) < 4:
        warnings.append("数值列命名不像至少四个复样列，可能是性状、评分或统计列。")
    replicate_groups = infer_sample_groups(replicate_columns)
    reusable_samples = sum(len(samples) for samples in replicate_groups.values())
    if len(replicate_groups) < 2 or reusable_samples < 4:
        warnings.append("未确认至少两个含复样的样本分组。")
    if len(groups) > len(replicate_groups) and len(replicate_groups) < 2:
        warnings.append("按列名前缀形成的分组缺少复样证据，不作为分析分组。")
    return ("high", []) if not warnings else ("low", warnings)


def _clean_frame(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame = frame.dropna(axis=0, how="all").dropna(axis=1, how="all")
    frame.columns = [str(column).strip() for column in frame.columns]
    frame = _preserve_feature_index_column(frame)
    frame = frame.loc[:, [not str(column).startswith("Unnamed:") for column in frame.columns]]
    return frame.reset_index(drop=True)


def _profile_file(item: UploadedFileSummary, path: Path) -> dict[str, Any]:
    profile = {
        "file_id": item.file_id,
        "filename": item.filename,
        "source_path": str(path),
        "intake_version": INTAKE_VERSION,
        "status": "unread",
        "data_family": "unknown",
        "data_type": "unknown_table",
        "confidence": "unconfirmed",
        "analysis_ready": False,
        "recommended_skills": [],
        "capabilities": [],
        "warnings": [],
        "reason": "",
    }
    try:
        frame = read_preview_table(path)
        sample_columns = detect_numeric_sample_columns(frame)
        if len(sample_columns) < 4:
            sample_columns = detect_numeric_sample_columns(frame, min_numeric_ratio=0.2)
        groups = infer_sample_groups(sample_columns)
        data_type = detect_data_type(frame)
        data_family = detect_data_family(frame, data_type)
        confidence, warnings = profile_confidence(data_family, data_type, sample_columns, groups)
        analysis_ready = confidence == "high" and data_type == "expression_matrix"
        profile_shape = {
            "data_family": data_family,
            "data_type": data_type,
            "confidence": confidence,
            "analysis_ready": analysis_ready,
        }
        profile.update(
            {
                "status": "profiled",
                "data_family": data_family,
                "data_type": data_type,
                "confidence": confidence,
                "analysis_ready": analysis_ready,
                "row_count_preview": int(len(frame)),
                "column_count": int(len(frame.columns)),
                "columns_preview": [str(column) for column in frame.columns[:30]],
                "numeric_sample_columns": sample_columns[:40],
                "sample_groups": groups,
                "recommended_skills": recommend_skills(data_family, data_type, confidence),
                "capabilities": capabilities_for_profile(profile_shape),
                "warnings": warnings,
                "reason": explain_profile(data_family, data_type, frame, sample_columns),
            }
        )
    except Exception as exc:
        profile.update({"status": "failed", "reason": str(exc)})
    return profile


def _expression_matrix_strategies() -> list[dict[str, Any]]:
    return [
        {"id": "numeric_samples_strict", "min_numeric_ratio": 0.55},
        {"id": "numeric_samples_relaxed", "min_numeric_ratio": 0.4},
        {"id": "numeric_samples_sparse", "min_numeric_ratio": 0.2},
    ]


def _standardize_expression_matrix(
    frame: pd.DataFrame,
    intake_dir: Path,
    data_family: str,
    strategy: dict[str, Any],
) -> dict[str, Any]:
    sample_columns = detect_numeric_sample_columns(
        frame,
        min_numeric_ratio=float(strategy["min_numeric_ratio"]),
    )
    if len(sample_columns) < 4:
        raise ValueError("可识别数值样本列少于 4 列")
    groups = infer_sample_groups(sample_columns)
    if len(groups) < 2 or sum(len(samples) for samples in groups.values()) < 4:
        raise ValueError("未识别到至少两个可复用样本分组")
    matrix = build_standard_matrix(frame, sample_columns, data_family)
    if matrix.empty:
        raise ValueError("标准矩阵没有有效 feature 行")
    metadata = pd.DataFrame(
        [
            {"sample": sample, "condition": condition}
            for condition, samples in groups.items()
            for sample in samples
            if sample in sample_columns
        ]
    )
    if metadata.empty:
        raise ValueError("样本 metadata 为空")
    matrix_path = intake_dir / "standard_matrix.csv"
    metadata_path = intake_dir / "sample_metadata.csv"
    matrix.to_csv(matrix_path, index=False)
    metadata.to_csv(metadata_path, index=False)
    return {
        "feature_count": int(len(matrix)),
        "sample_count": int(len(sample_columns)),
        "sample_columns": sample_columns,
        "sample_groups": groups,
        "standard_files": {
            "matrix": str(matrix_path),
            "sample_metadata": str(metadata_path),
        },
    }


def _failed_intake(item: UploadedFileSummary, reason: str) -> dict[str, Any]:
    return {
        "file_id": item.file_id,
        "filename": item.filename,
        "status": "failed",
        "data_family": "unknown",
        "data_type": "unknown_table",
        "confidence": "unconfirmed",
        "analysis_ready": False,
        "recommended_skills": [],
        "capabilities": [],
        "warnings": [],
        "reason": reason,
    }


def _numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.strip(),
        errors="coerce",
    )


def _normalized_column_name(column: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(column).strip().lower()).strip("_")


def _looks_like_replicate_sample(sample: str) -> bool:
    value = str(sample).strip()
    return bool(
        re.search(r"(?:^|[_\-. ])(?:rep|sample)?\d+(?:[_\-. ][A-Za-z]+)?$", value, re.I)
        or re.search(r"[A-Za-z][_\-. ]?[A-Za-z]*\d+(?:[_\-. ][A-Za-z]+)?$", value)
    )


def _first_matching_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    lowered = {str(column).lower(): str(column) for column in frame.columns}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    for column in frame.columns:
        value = str(column).lower()
        if any(candidate.lower() in value for candidate in candidates):
            return str(column)
    return None


def _looks_like_annotation_column(lowered: str) -> bool:
    return any(
        keyword in lowered
        for keyword in (
            "protein",
            "gene",
            "description",
            "annotation",
            "sequence",
            "peptide",
            "id",
            "accession",
            "name",
            "biotype",
            "strand",
            "locus",
        )
    )


def _preserve_feature_index_column(frame: pd.DataFrame) -> pd.DataFrame:
    if "feature_id" in frame.columns:
        return frame
    for column in frame.columns:
        if str(column).startswith("Unnamed:") and _looks_like_feature_ids(frame[column]):
            return frame.rename(columns={column: "feature_id"})
    return frame


def _looks_like_feature_ids(series: pd.Series) -> bool:
    values = series.astype(str).str.strip()
    values = values[values.ne("") & values.str.lower().ne("nan")]
    if len(values) < 2:
        return False
    numeric_ratio = _numeric_series(values).notna().sum() / len(values)
    unique_ratio = values.nunique() / len(values)
    return numeric_ratio < 0.2 and unique_ratio >= 0.5


def _looks_like_gene_ids(series: pd.Series) -> bool:
    values = series.astype(str).str.strip()
    values = values[values.ne("") & values.str.lower().ne("nan")].head(100)
    if len(values) < 2:
        return False
    patterns = (
        r"(?:AGIS_)?Os\d{2}g\d+",
        r"LOC_[A-Za-z0-9]+g\d+",
        r"AT[1-5CM]G\d+",
        r"Glyma\.\d+G\d+",
        r"Zm\d+(?:d|eb)\d+",
        r"ENS[A-Z]*G\d+",
    )
    matches = values.str.match("^(?:" + "|".join(patterns) + r")", case=False).sum()
    return matches >= max(2, math.ceil(len(values) * 0.5))

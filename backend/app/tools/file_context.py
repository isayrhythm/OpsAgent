from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd
from pypdf import PdfReader

from backend.app.llm.calls import chat_json
from backend.app.llm.prompts import FILE_TRANSFORMER_SYSTEM_PROMPT
from backend.app.schemas import UploadedFileSummary
from backend.app.services.skill_loader import SkillSpec
from backend.app.skill_tools.blast_query import (
    FASTA_SUFFIXES,
    MAX_QUERY_SEQUENCES,
    detect_sequence_type,
    normalize_sequence,
    parse_fasta_text,
)


SUPPORTED_TABLE_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xls", ".xlsm"}
SUPPORTED_PDF_SUFFIXES = {".pdf"}
SUPPORTED_FASTA_SUFFIXES = FASTA_SUFFIXES
DEFAULT_MAX_INTAKE_ATTEMPTS = 3
INTAKE_VERSION = 5
PDF_TEXT_EXCERPT_CHARS = 12000
PDF_FULL_TEXT_CHARS = 80000
PDF_MAX_PAGES = 80
TABLE_PREVIEW_ROWS = 2000
TABLE_SAMPLE_ROWS = 5


def ensure_attachment_intakes(attachments: list[UploadedFileSummary]) -> list[UploadedFileSummary]:
    ready: list[UploadedFileSummary] = []
    for item in attachments:
        if item.intake and item.intake.get("intake_version") == INTAKE_VERSION:
            ready.append(item)
            continue
        ready.append(item.model_copy(update={"intake": inspect_uploaded_file(item)}))
    return ready


def inspect_uploaded_file(item: UploadedFileSummary) -> dict[str, Any]:
    """上传后的第一层文件上下文：只观察文件形态，不绑定业务 skill。"""
    if not item.path:
        return _failed_intake(item, "上传文件缺少保存路径。")
    path = Path(item.path)
    if path.suffix.lower() in SUPPORTED_PDF_SUFFIXES:
        return intake_pdf_file(item, path)
    if path.suffix.lower() in SUPPORTED_FASTA_SUFFIXES:
        return intake_fasta_file(item, path)
    if path.suffix.lower() not in SUPPORTED_TABLE_SUFFIXES:
        return {
            "status": "skipped",
            "intake_version": INTAKE_VERSION,
            "file_kind": "unsupported",
            "format": path.suffix.lower().lstrip(".") or "unknown",
            "data_family": "unknown",
            "data_type": "unsupported_file",
            "confidence": "unconfirmed",
            "analysis_ready": False,
            "reason": "当前 intake 只处理 CSV/TSV/TXT/XLSX 表格、PDF 文献和 FASTA 序列。",
            "warnings": [],
            "capabilities": [],
        }

    return inspect_table_file(item, path)


def intake_uploaded_file(
    item: UploadedFileSummary,
    max_attempts: int = DEFAULT_MAX_INTAKE_ATTEMPTS,
) -> dict[str, Any]:
    """兼容旧调用名；现在上传阶段只做 File Inspector。"""
    return inspect_uploaded_file(item)


def inspect_table_file(item: UploadedFileSummary, path: Path) -> dict[str, Any]:
    profile = _base_file_context(item, path, file_kind="table")
    try:
        frame = read_preview_table(path, max_rows=TABLE_PREVIEW_ROWS)
        numeric_columns = detect_numeric_sample_columns(frame)
        if len(numeric_columns) < 4:
            numeric_columns = detect_numeric_sample_columns(frame, min_numeric_ratio=0.2)
        profile.update(
            {
                "status": "profiled",
                "confidence": "unconfirmed",
                "data_type": "table",
                "shape": {
                    "rows_preview": int(len(frame)),
                    "columns": int(len(frame.columns)),
                    "preview_truncated": int(len(frame)) >= TABLE_PREVIEW_ROWS,
                },
                "row_count_preview": int(len(frame)),
                "column_count": int(len(frame.columns)),
                "columns": [str(column) for column in frame.columns[:50]],
                "columns_preview": [str(column) for column in frame.columns[:50]],
                "sample_preview": _table_sample_preview(frame),
                "numeric_columns_preview": numeric_columns[:40],
                "possible_sample_groups": infer_sample_groups(numeric_columns),
                "capabilities": ["table_preview"],
                "reason": f"已生成通用表格上下文：预览 {len(frame)} 行、{len(frame.columns)} 列。",
            }
        )
    except Exception as exc:
        profile.update({"status": "failed", "reason": str(exc), "warnings": ["table_preview_failed"]})
    return profile


async def transform_attachments_for_skill(
    attachments: list[UploadedFileSummary],
    skill: SkillSpec,
    llm: Any | None = None,
    max_attempts: int = DEFAULT_MAX_INTAKE_ATTEMPTS,
) -> list[UploadedFileSummary]:
    """第二层 File Transformer：根据目标 skill 已有契约，把文件转成执行器需要的结构。"""
    if not _skill_needs_file_transform(skill):
        return attachments

    transformed: list[UploadedFileSummary] = []
    for item in attachments:
        plan = await plan_file_transform(item, skill, llm)
        transformed_intake = transform_uploaded_file_for_skill(
            item,
            skill,
            transform_plan=plan,
            max_attempts=max_attempts,
        )
        if transformed_intake is None:
            transformed.append(item)
        else:
            transformed.append(item.model_copy(update={"intake": transformed_intake}))
    return transformed


async def plan_file_transform(
    item: UploadedFileSummary,
    skill: SkillSpec,
    llm: Any | None = None,
) -> dict[str, Any]:
    file_context = item.intake or inspect_uploaded_file(item)
    if llm is not None and getattr(llm, "available", False):
        try:
            response = await chat_json(
                llm,
                [
                    {"role": "system", "content": FILE_TRANSFORMER_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "target_skill": _skill_contract_payload(skill),
                                "file_context": file_context,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                model=llm.settings.router_model,
                temperature=0,
                max_tokens=900,
            )
            return _sanitize_transform_plan(response, item, skill)
        except Exception:
            pass
    return _deterministic_transform_plan(item, skill)


def transform_uploaded_file_for_skill(
    item: UploadedFileSummary,
    skill: SkillSpec | dict[str, Any],
    transform_plan: dict[str, Any] | None = None,
    max_attempts: int = DEFAULT_MAX_INTAKE_ATTEMPTS,
) -> dict[str, Any] | None:
    if not _skill_needs_file_transform(skill):
        return None
    target_family = _target_family_from_skill(skill)
    if target_family is None:
        return None
    if not item.path:
        return _failed_intake(item, "上传文件缺少保存路径，无法转换为分析输入。")
    path = Path(item.path)
    if path.suffix.lower() not in SUPPORTED_TABLE_SUFFIXES:
        return None

    profile = _profile_file(item, path)
    target_adapter = _target_adapter_from_skill(skill)
    if target_adapter != "differential_analysis_input":
        return None
    plan = transform_plan or _deterministic_transform_plan(item, skill)
    profile["adapter"] = {
        "target_skill": _skill_name(skill),
        "target_adapter": target_adapter,
        "plan": plan,
    }
    if (
        profile["status"] != "profiled"
        or profile["data_type"] != "expression_matrix"
        or profile.get("confidence") != "high"
    ):
        return {
            **profile,
            "analysis_ready": False,
            "recommended_skills": [],
            "capabilities": [],
        }
    if profile.get("data_family") != target_family:
        return {
            **profile,
            "analysis_ready": False,
            "recommended_skills": [],
            "capabilities": [],
            "warnings": [
                *(profile.get("warnings") or []),
                f"文件更像 {profile.get('data_family')}，不匹配 {_skill_name(skill)} 需要的 {target_family}。",
            ],
            "reason": f"文件类型与目标 skill 不匹配：{profile.get('data_family')} -> {target_family}。",
        }

    try:
        frame = read_table(path)
    except Exception as exc:
        return {**profile, "status": "failed", "reason": f"读取原始文件失败：{exc}", "capabilities": []}

    attempts: list[dict[str, Any]] = []
    intake_dir = path.parent / f"{item.file_id}_intake"
    intake_dir.mkdir(parents=True, exist_ok=True)
    for index, strategy in enumerate(_expression_matrix_strategies()[:max_attempts], start=1):
        try:
            standard = _standardize_expression_matrix(
                frame,
                intake_dir,
                target_family,
                strategy,
                transform_plan=plan,
            )
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
                    "file_kind": "table",
                    "format": path.suffix.lower().lstrip(".") or "unknown",
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
            (intake_dir / "file_transform_output.json").write_text(
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
        "reason": f"{max_attempts} 次 File Transformer 后仍未得到可用于分析的标准矩阵。",
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
        if not item.path:
            continue
        path = Path(item.path)
        suffix = path.suffix.lower()
        if suffix in SUPPORTED_PDF_SUFFIXES:
            profiles.append(prompt_profile(item, intake_pdf_file(item, path)))
        elif suffix in SUPPORTED_FASTA_SUFFIXES:
            profiles.append(prompt_profile(item, intake_fasta_file(item, path)))
        elif suffix in SUPPORTED_TABLE_SUFFIXES:
            profiles.append(prompt_profile(item, inspect_table_file(item, path)))
    return profiles


def prompt_profile(item: UploadedFileSummary, intake: dict[str, Any]) -> dict[str, Any]:
    profile = {
        "file_id": item.file_id,
        "filename": item.filename,
        "content_type": item.content_type,
        "size": item.size,
        "path": item.path,
        "file_kind": intake.get("file_kind"),
        "format": intake.get("format"),
        "shape": intake.get("shape", {}),
        "status": intake.get("status", "unknown"),
        "data_family": intake.get("data_family", "unknown"),
        "data_type": intake.get("data_type", "unknown_table"),
        "confidence": intake.get("confidence", "unconfirmed"),
        "analysis_ready": bool(intake.get("analysis_ready")),
        "reason": intake.get("reason", ""),
        "warnings": intake.get("warnings", []),
        "columns": intake.get("columns", []),
        "columns_preview": intake.get("columns_preview", []),
        "sample_preview": intake.get("sample_preview", []),
        "numeric_columns_preview": intake.get("numeric_columns_preview", []),
        "possible_sample_groups": intake.get("possible_sample_groups", {}),
        "sample_groups": intake.get("sample_groups", {}),
        "feature_count": intake.get("feature_count"),
        "sample_count": intake.get("sample_count"),
        "recommended_skills": intake.get("recommended_skills", []),
        "capabilities": intake.get("capabilities", []),
        "standard_files": intake.get("standard_files", {}),
        "page_count": intake.get("page_count"),
        "parsed_pages": intake.get("parsed_pages"),
        "text_length": intake.get("text_length"),
        "title": intake.get("title"),
        "text_excerpt": intake.get("text_excerpt"),
        "text_file": intake.get("text_file"),
        "sequence_count": intake.get("sequence_count"),
        "sequences_preview": intake.get("sequences_preview", []),
    }
    return {key: value for key, value in profile.items() if value not in (None, "", [], {})}


def intake_fasta_file(item: UploadedFileSummary, path: Path) -> dict[str, Any]:
    profile = {
        "file_id": item.file_id,
        "filename": item.filename,
        "source_path": str(path),
        "intake_version": INTAKE_VERSION,
        "file_kind": "sequence",
        "format": path.suffix.lower().lstrip(".") or "fasta",
        "status": "failed",
        "data_family": "sequence",
        "data_type": "fasta_sequences",
        "confidence": "unconfirmed",
        "analysis_ready": False,
        "recommended_skills": [],
        "capabilities": [],
        "warnings": [],
        "reason": "",
    }
    try:
        records = parse_fasta_text(path.read_text(encoding="utf-8-sig"), source=item.filename)
    except Exception as exc:
        return {**profile, "reason": f"FASTA 解析失败：{exc}"}
    if not records:
        return {**profile, "reason": "FASTA 文件中没有识别到带 > 标签的序列。"}
    if len(records) > MAX_QUERY_SEQUENCES:
        return {**profile, "reason": f"FASTA 最多支持 {MAX_QUERY_SEQUENCES} 条序列，当前文件包含 {len(records)} 条。"}

    previews = []
    for label, raw_sequence, _source in records:
        sequence = normalize_sequence(raw_sequence)
        sequence_type = detect_sequence_type(sequence)
        if sequence_type == "UNKNOWN":
            return {**profile, "reason": f"序列 {label} 含有不支持的字符，或无法判断 DNA/RNA/蛋白质类型。"}
        previews.append({"label": label, "sequence_type": sequence_type, "length": len(sequence)})

    return {
        **profile,
        "status": "ready",
        "confidence": "high",
        "sequence_count": len(previews),
        "sequences_preview": previews,
        "recommended_skills": [],
        "capabilities": ["sequence_preview"],
        "reason": f"已识别 {len(previews)} 条 FASTA 序列，已生成通用序列上下文。",
    }


def intake_pdf_file(item: UploadedFileSummary, path: Path) -> dict[str, Any]:
    profile = {
        "file_id": item.file_id,
        "filename": item.filename,
        "source_path": str(path),
        "intake_version": INTAKE_VERSION,
        "file_kind": "pdf",
        "format": "pdf",
        "status": "failed",
        "data_family": "document",
        "data_type": "pdf_document",
        "confidence": "unconfirmed",
        "analysis_ready": False,
        "recommended_skills": [],
        "capabilities": [],
        "warnings": [],
        "reason": "",
    }
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                pass
            if reader.is_encrypted:
                return {
                    **profile,
                    "reason": "PDF 已加密，无法提取文本。",
                    "warnings": ["encrypted_pdf"],
                }

        page_count = len(reader.pages)
        page_texts = []
        warnings = []
        for page_index, page in enumerate(reader.pages[:PDF_MAX_PAGES], start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                warnings.append(f"page_{page_index}_extract_failed: {exc}")
                text = ""
            text = _normalize_pdf_text(text)
            if text:
                page_texts.append(text)

        full_text = _normalize_pdf_text("\n\n".join(page_texts))
        if not full_text:
            return {
                **profile,
                "page_count": page_count,
                "parsed_pages": min(page_count, PDF_MAX_PAGES),
                "reason": "PDF 未提取到可用文本，可能是扫描版、图片型 PDF、加密文档或文本编码不可解析。",
                "warnings": warnings or ["no_extractable_text"],
            }

        if page_count > PDF_MAX_PAGES:
            warnings.append(f"only_first_{PDF_MAX_PAGES}_pages_parsed")

        intake_dir = path.parent / f"{item.file_id}_pdf_intake"
        intake_dir.mkdir(parents=True, exist_ok=True)
        text_file = intake_dir / "extracted_text.txt"
        text_file.write_text(full_text[:PDF_FULL_TEXT_CHARS], encoding="utf-8")

        title = _pdf_metadata_text(getattr(reader, "metadata", None), "/Title")
        return {
            **profile,
            "status": "ready",
            "confidence": "high",
            "page_count": page_count,
            "parsed_pages": min(page_count, PDF_MAX_PAGES),
            "text_length": len(full_text),
            "title": title,
            "text_excerpt": full_text[:PDF_TEXT_EXCERPT_CHARS],
            "text_file": str(text_file),
            "capabilities": ["text_extraction"],
            "warnings": warnings,
            "reason": f"已提取 PDF 文本：{min(page_count, PDF_MAX_PAGES)}/{page_count} 页，约 {len(full_text)} 字符。",
        }
    except Exception as exc:
        return {
            **profile,
            "reason": f"PDF 解析失败：{exc}",
            "warnings": ["pdf_parse_failed"],
        }


def pdf_context_for_history(attachments: list[UploadedFileSummary]) -> str:
    sections = []
    for item in attachments:
        intake = item.intake or {}
        if intake.get("data_type") != "pdf_document":
            continue
        if intake.get("status") != "ready":
            sections.append(
                "\n".join(
                    [
                        f"[PDF attachment: {item.filename}]",
                        f"status: {intake.get('status', 'unknown')}",
                        f"reason: {intake.get('reason', '')}",
                    ]
                )
            )
            continue
        sections.append(
            "\n".join(
                [
                    f"[PDF attachment: {item.filename}]",
                    f"path: {item.path or intake.get('source_path', '')}",
                    f"title: {intake.get('title') or ''}",
                    f"pages: {intake.get('parsed_pages', '?')}/{intake.get('page_count', '?')}",
                    f"text_file: {intake.get('text_file', '')}",
                    "text_excerpt:",
                    str(intake.get("text_excerpt") or ""),
                ]
            )
        )
    if not sections:
        return ""
    return "PDF 文献上下文（由上传文件解析得到，后续回答可引用；不要编造未出现的信息）：\n" + "\n\n".join(sections)


def _base_file_context(item: UploadedFileSummary, path: Path, *, file_kind: str) -> dict[str, Any]:
    return {
        "file_id": item.file_id,
        "filename": item.filename,
        "source_path": str(path),
        "intake_version": INTAKE_VERSION,
        "file_kind": file_kind,
        "format": path.suffix.lower().lstrip(".") or "unknown",
        "status": "unread",
        "data_family": "unknown",
        "data_type": file_kind,
        "confidence": "unconfirmed",
        "analysis_ready": False,
        "recommended_skills": [],
        "capabilities": [],
        "warnings": [],
        "reason": "",
    }


def _table_sample_preview(frame: pd.DataFrame) -> list[dict[str, Any]]:
    preview = frame.head(TABLE_SAMPLE_ROWS)
    rows: list[dict[str, Any]] = []
    for record in preview.to_dict(orient="records"):
        rows.append(
            {
                str(key): None if _is_missing_value(value) else str(value)[:200]
                for key, value in record.items()
            }
        )
    return rows


def _is_missing_value(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except Exception:
        return value is None


def _skill_name(skill: SkillSpec | dict[str, Any]) -> str:
    if isinstance(skill, SkillSpec):
        return skill.name
    return str(skill.get("name") or skill.get("target_skill") or "unknown")


def _skill_text(skill: SkillSpec | dict[str, Any]) -> str:
    if isinstance(skill, SkillSpec):
        return "\n".join(
            [
                skill.name,
                skill.description,
                skill.trigger,
                " ".join(skill.data_paths),
                skill.content,
            ]
        ).lower()
    return json.dumps(skill, ensure_ascii=False).lower()


def _skill_needs_file_transform(skill: SkillSpec | dict[str, Any]) -> bool:
    text = _skill_text(skill)
    return (
        "standard_matrix.csv" in text
        and "sample_metadata.csv" in text
        and ("differential" in text or "差异" in text)
    )


def _target_adapter_from_skill(skill: SkillSpec | dict[str, Any]) -> str:
    return "differential_analysis_input" if _skill_needs_file_transform(skill) else ""


def _target_family_from_skill(skill: SkillSpec | dict[str, Any]) -> str | None:
    text = _skill_text(skill)
    if "proteomics" in text or "protein" in text or "蛋白组" in text or "蛋白" in text:
        return "proteomics"
    if "transcriptomics" in text or "rna-seq" in text or "counts" in text or "转录组" in text:
        return "transcriptomics"
    return None


def _skill_contract_payload(skill: SkillSpec) -> dict[str, Any]:
    return {
        "name": skill.name,
        "description": skill.description,
        "trigger": skill.trigger,
        "execution_mode": skill.execution_mode,
        "data_paths": skill.data_paths,
        "input_schema": skill.input_schema,
        "content": skill.content[:16000],
    }


def _sanitize_transform_plan(
    value: Any,
    item: UploadedFileSummary,
    skill: SkillSpec,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _deterministic_transform_plan(item, skill)
    plan = {
        "selected_file_id": str(value.get("selected_file_id") or item.file_id),
        "target_adapter": str(value.get("target_adapter") or _target_adapter_from_skill(skill)),
        "target_data_family": str(
            value.get("target_data_family") or _target_family_from_skill(skill) or ""
        ),
        "confidence": "high" if value.get("confidence") == "high" else "low",
        "feature_id_column": str(value.get("feature_id_column") or ""),
        "feature_name_column": str(value.get("feature_name_column") or ""),
        "description_column": str(value.get("description_column") or ""),
        "sample_columns": [str(item) for item in value.get("sample_columns") or []],
        "sample_groups": _string_list_mapping(value.get("sample_groups")),
        "missing_requirements": [str(item) for item in value.get("missing_requirements") or []],
        "reason": str(value.get("reason") or ""),
    }
    if plan["selected_file_id"] != item.file_id:
        plan["confidence"] = "low"
        plan["missing_requirements"].append("selected_file_id does not match current file")
    return plan


def _deterministic_transform_plan(
    item: UploadedFileSummary,
    skill: SkillSpec | dict[str, Any],
) -> dict[str, Any]:
    target_family = _target_family_from_skill(skill)
    target_adapter = _target_adapter_from_skill(skill)
    if not item.path:
        return {
            "selected_file_id": item.file_id,
            "target_adapter": target_adapter,
            "target_data_family": target_family or "",
            "confidence": "low",
            "sample_columns": [],
            "sample_groups": {},
            "missing_requirements": ["missing file path"],
            "reason": "上传文件缺少路径。",
        }
    try:
        frame = read_preview_table(Path(item.path))
        sample_columns = detect_numeric_sample_columns(frame)
        if len(sample_columns) < 4:
            sample_columns = detect_numeric_sample_columns(frame, min_numeric_ratio=0.2)
        data_type = detect_data_type(frame)
        detected_family = detect_data_family(frame, data_type)
        groups = infer_sample_groups(sample_columns)
        confidence, warnings = profile_confidence(detected_family, data_type, sample_columns, groups)
        missing = warnings[:]
        if detected_family != target_family:
            missing.append(f"detected family {detected_family} does not match target {target_family}")
        return {
            "selected_file_id": item.file_id,
            "target_adapter": target_adapter,
            "target_data_family": target_family or "",
            "confidence": "high" if confidence == "high" and detected_family == target_family else "low",
            "feature_id_column": _first_matching_column(
                frame,
                _feature_id_candidates(target_family),
            )
            or "",
            "feature_name_column": _first_matching_column(
                frame,
                _feature_name_candidates(target_family),
            )
            or "",
            "description_column": _first_matching_column(
                frame,
                _description_candidates(target_family),
            )
            or "",
            "sample_columns": sample_columns,
            "sample_groups": groups,
            "missing_requirements": missing,
            "reason": "LLM 不可用时使用本地确定性 schema 检测生成转换计划。",
        }
    except Exception as exc:
        return {
            "selected_file_id": item.file_id,
            "target_adapter": target_adapter,
            "target_data_family": target_family or "",
            "confidence": "low",
            "sample_columns": [],
            "sample_groups": {},
            "missing_requirements": [str(exc)],
            "reason": "文件转换计划生成失败。",
        }


def _feature_id_candidates(data_family: str | None) -> tuple[str, ...]:
    if data_family == "proteomics":
        return ("Protein.Names", "Protein.Group", "Protein.IDs", "protein", "Genes", "gene", "feature_id", "id")
    if data_family == "transcriptomics":
        return ("gene_id", "gene", "locus", "transcript", "feature_id", "id")
    return ("feature_id", "id", "gene", "protein", "name")


def _feature_name_candidates(data_family: str | None) -> tuple[str, ...]:
    if data_family == "proteomics":
        return ("Genes", "gene", "Protein.Names", "protein", "name")
    if data_family == "transcriptomics":
        return ("gene_short_name", "gene_name", "gene", "symbol", "name")
    return ("name", "gene", "protein", "feature_id", "id")


def _description_candidates(data_family: str | None) -> tuple[str, ...]:
    if data_family == "proteomics":
        return ("First.Protein.Description", "description", "annotation")
    if data_family == "transcriptomics":
        return ("description", "annotation", "biotype")
    return ("description", "annotation")


def _string_list_mapping(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, list[str]] = {}
    for key, items in value.items():
        if not isinstance(items, list):
            continue
        strings = [str(item) for item in items if str(item).strip()]
        if strings:
            cleaned[str(key)] = strings
    return cleaned


def _normalize_pdf_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _pdf_metadata_text(metadata: Any, key: str) -> str:
    if not metadata:
        return ""
    try:
        value = metadata.get(key)
    except Exception:
        value = None
    return str(value or "").strip()


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
    sections = ["当前会话已上传文件。以下是 File Inspector 生成的通用文件上下文，不包含完整原始内容："]
    for item in attachments:
        intake = item.intake or {}
        if intake.get("data_type") == "pdf_document":
            sections.append(_uploaded_pdf_prompt(item, intake))
            continue
        if intake.get("data_type") == "fasta_sequences":
            sections.append(_uploaded_fasta_prompt(item, intake))
            continue
        groups = intake.get("sample_groups") or {}
        possible_groups = intake.get("possible_sample_groups") or groups
        group_text = ", ".join(f"{group}({len(samples)})" for group, samples in possible_groups.items()) or "未识别"
        columns = ", ".join(str(column) for column in (intake.get("columns") or intake.get("columns_preview") or [])[:12]) or "未识别"
        numeric_columns = ", ".join(str(column) for column in (intake.get("numeric_columns_preview") or [])[:12]) or "未识别"
        warnings = "；".join(str(warning) for warning in (intake.get("warnings") or [])) or "无"
        shape = intake.get("shape") or {}
        sections.append(
            "\n".join(
                [
                    f"- 文件名：{item.filename}",
                    f"  文件位置：{item.path or 'unknown'}",
                    f"  文件大小：{item.size} bytes",
                    f"  文件上下文状态：{intake.get('status', 'not_processed')}",
                    f"  文件形态：{intake.get('file_kind', 'unknown')}/{intake.get('format', 'unknown')}",
                    f"  表格规模预览：rows={shape.get('rows_preview', intake.get('row_count_preview', 'unknown'))}; columns={shape.get('columns', intake.get('column_count', 'unknown'))}",
                    f"  结构警告：{warnings}",
                    f"  列名预览：{columns}",
                    f"  数值列预览：{numeric_columns}",
                    f"  可能分组：{group_text}",
                ]
            )
        )
    return "\n".join(sections)


def _uploaded_pdf_prompt(item: UploadedFileSummary, intake: dict[str, Any]) -> str:
    if intake.get("status") != "ready":
        return "\n".join(
            [
                f"- 文件名：{item.filename}",
                f"  文件位置：{item.path or 'unknown'}",
                f"  文件大小：{item.size} bytes",
                f"  文件上下文状态：{intake.get('status', 'not_processed')}",
                "  文件形态：pdf/pdf",
                f"  解析结果：{intake.get('reason', 'PDF 文本解析失败')}",
            ]
        )
    excerpt = str(intake.get("text_excerpt") or "")
    return "\n".join(
        [
            f"- 文件名：{item.filename}",
            f"  文件位置：{item.path or 'unknown'}",
            f"  文件大小：{item.size} bytes",
            "  文件形态：pdf/pdf",
            f"  文件上下文状态：{intake.get('status', 'ready')}",
            f"  标题：{intake.get('title') or 'unknown'}",
            f"  页数：{intake.get('parsed_pages', 'unknown')}/{intake.get('page_count', 'unknown')}",
            f"  提取文本长度：{intake.get('text_length', 'unknown')}",
            f"  全文文本位置：{intake.get('text_file', 'not_ready')}",
            "  PDF 文本摘录：",
            excerpt,
        ]
    )


def _uploaded_fasta_prompt(item: UploadedFileSummary, intake: dict[str, Any]) -> str:
    previews = intake.get("sequences_preview") or []
    preview_text = ", ".join(
        f"{entry.get('label', 'query')}({entry.get('sequence_type', 'unknown')}, {entry.get('length', '?')})"
        for entry in previews[:MAX_QUERY_SEQUENCES]
        if isinstance(entry, dict)
    ) or "未识别"
    return "\n".join(
        [
            f"- 文件名：{item.filename}",
            f"  文件位置：{item.path or 'unknown'}",
            f"  文件大小：{item.size} bytes",
            "  文件形态：sequence/fasta",
            f"  文件上下文状态：{intake.get('status', 'not_processed')}",
            f"  解析结果：{intake.get('reason', '')}",
            f"  序列数量：{intake.get('sequence_count', 0)}",
            f"  序列预览：{preview_text}",
        ]
    )


def build_standard_matrix(
    frame: pd.DataFrame,
    sample_columns: list[str],
    data_family: str,
) -> pd.DataFrame:
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
    transform_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sample_columns = _planned_sample_columns(frame, transform_plan)
    if not sample_columns:
        sample_columns = detect_numeric_sample_columns(
            frame,
            min_numeric_ratio=float(strategy["min_numeric_ratio"]),
        )
    if len(sample_columns) < 4:
        raise ValueError("可识别数值样本列少于 4 列")
    groups = _planned_sample_groups(sample_columns, transform_plan) or infer_sample_groups(sample_columns)
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


def _planned_sample_columns(frame: pd.DataFrame, transform_plan: dict[str, Any] | None) -> list[str]:
    if not transform_plan:
        return []
    available = {str(column): str(column) for column in frame.columns}
    columns: list[str] = []
    for column in transform_plan.get("sample_columns") or []:
        name = str(column)
        if name in available and name not in columns:
            values = _numeric_series(frame[available[name]])
            if values.notna().sum() >= 2:
                columns.append(available[name])
    return columns


def _planned_sample_groups(
    sample_columns: list[str],
    transform_plan: dict[str, Any] | None,
) -> dict[str, list[str]]:
    if not transform_plan:
        return {}
    available = set(sample_columns)
    groups: dict[str, list[str]] = {}
    for group, samples in _string_list_mapping(transform_plan.get("sample_groups")).items():
        kept = [sample for sample in samples if sample in available]
        if len(kept) >= 2:
            groups[group] = kept
    return groups


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

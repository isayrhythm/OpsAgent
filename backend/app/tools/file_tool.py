from __future__ import annotations

from typing import Any

from backend.app.schemas import UploadedFileSummary
from backend.app.tools.file_context import inspect_uploaded_file, transform_uploaded_file_for_skill


FILE_INSPECTOR_NAME = "File Inspector"
FILE_TRANSFORMER_NAME = "File Transformer"

FILE_INSPECTOR_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["file_id", "filename", "file_kind", "format", "status"],
    "properties": {
        "file_id": {"type": "string"},
        "filename": {"type": "string"},
        "file_kind": {"type": "string"},
        "format": {"type": "string"},
        "status": {"type": "string"},
        "shape": {"type": "object"},
        "columns": {"type": "array", "items": {"type": "string"}},
        "sample_preview": {"type": "array", "items": {"type": "object"}},
        "text_excerpt": {"type": "string"},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}

FILE_TRANSFORMER_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["target_skill"],
    "properties": {
        "target_skill": {"type": "string"},
        "file_ids": {"type": "array", "items": {"type": "string"}},
    },
}

FILE_TRANSFORMER_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["target_skill", "attachments"],
    "properties": {
        "target_skill": {"type": "string"},
        "attachments": {"type": "array", "items": {"type": "object"}},
    },
}


def inspect_file(item: UploadedFileSummary) -> dict[str, Any]:
    return inspect_uploaded_file(item)


def transform_files_for_skill(
    attachments: list[UploadedFileSummary],
    target_skill: dict[str, Any],
) -> dict[str, Any]:
    transformed = []
    for item in attachments:
        intake = transform_uploaded_file_for_skill(item, target_skill)
        transformed.append(item.model_copy(update={"intake": intake}) if intake else item)
    return {
        "target_skill": str(target_skill.get("name") or "unknown"),
        "attachments": [item.model_dump(mode="json") for item in transformed],
    }

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from backend.app.config import MEMORY_DIR
from backend.app.schemas import ChatHistoryMessage, UploadedFileSummary


def _safe_segment(value: str) -> str:
    value = value.strip() or "default"
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value)[:80]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class MemoryPaths:
    root: Path = MEMORY_DIR

    def user_short_root(self, user_id: str) -> Path:
        return self.root / "short_term" / _safe_segment(user_id)

    def conversation_path(self, user_id: str, session_id: str) -> Path:
        return self.user_short_root(user_id) / "conversations" / f"{_safe_segment(session_id)}.json"

    def uploads_dir(self, user_id: str) -> Path:
        return self.user_short_root(user_id) / "uploads"

    def upload_session_dir(self, user_id: str, session_id: str) -> Path:
        return self.uploads_dir(user_id) / _safe_segment(session_id)

    def long_term_profile_path(self, user_id: str) -> Path:
        return self.root / "long_term" / _safe_segment(user_id) / "profile.json"


class MemoryStore:
    def __init__(self, paths: MemoryPaths | None = None) -> None:
        self.paths = paths or MemoryPaths()

    def ensure_user_dirs(self, user_id: str) -> None:
        self.paths.user_short_root(user_id).mkdir(parents=True, exist_ok=True)
        (self.paths.user_short_root(user_id) / "conversations").mkdir(parents=True, exist_ok=True)
        self.paths.uploads_dir(user_id).mkdir(parents=True, exist_ok=True)
        self.paths.long_term_profile_path(user_id).parent.mkdir(parents=True, exist_ok=True)

    def load_history(self, user_id: str, session_id: str, limit: int = 20) -> list[ChatHistoryMessage]:
        path = self.paths.conversation_path(user_id, session_id)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        messages = payload.get("messages", [])
        return [
            ChatHistoryMessage(role=item["role"], content=item["content"])
            for item in messages[-limit:]
            if item.get("role") in {"user", "assistant", "agent"} and item.get("content")
        ]

    def append_exchange(
        self,
        user_id: str,
        session_id: str,
        user_message: str,
        answer: str,
        attachments: list[UploadedFileSummary] | None = None,
    ) -> None:
        self.ensure_user_dirs(user_id)
        path = self.paths.conversation_path(user_id, session_id)
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            payload = {
                "user_id": user_id,
                "session_id": session_id,
                "created_at": _now(),
                "messages": [],
            }

        payload["updated_at"] = _now()
        if attachments:
            payload["uploaded_files"] = [item.model_dump(mode="json") for item in attachments]
        payload.setdefault("messages", []).extend(
            [
                {"role": "user", "content": user_message, "created_at": _now()},
                {"role": "assistant", "content": answer, "created_at": _now()},
            ]
        )
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_upload(
        self,
        user_id: str,
        session_id: str,
        filename: str,
        content_type: str | None,
        source: BinaryIO,
    ) -> UploadedFileSummary:
        self.ensure_user_dirs(user_id)
        upload_dir = self.paths.upload_session_dir(user_id, session_id)
        upload_dir.mkdir(parents=True, exist_ok=True)

        safe_name = _safe_segment(Path(filename or "upload").name)
        file_id = uuid.uuid4().hex
        target = upload_dir / f"{file_id}_{safe_name}"
        with target.open("wb") as handle:
            shutil.copyfileobj(source, handle)

        summary = UploadedFileSummary(
            file_id=file_id,
            filename=Path(filename or "upload").name,
            content_type=content_type,
            size=target.stat().st_size,
            path=str(target),
        )
        metadata_path = upload_dir / f"{file_id}.json"
        metadata_path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
        return summary

    def update_upload_metadata(self, summary: UploadedFileSummary) -> None:
        if not summary.path:
            return
        metadata_path = Path(summary.path).parent / f"{summary.file_id}.json"
        metadata_path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")

    def load_uploads(self, user_id: str, session_id: str) -> list[UploadedFileSummary]:
        upload_dir = self.paths.upload_session_dir(user_id, session_id)
        if not upload_dir.exists():
            return []
        uploads: list[UploadedFileSummary] = []
        for path in sorted(upload_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            uploads.append(UploadedFileSummary.model_validate(payload))
        return uploads

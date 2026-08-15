"""Versioned TXT/Markdown/DOCX source import with non-destructive previews."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

from .media import DOCUMENT_EXTENSIONS, MediaService


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _slug(value: str) -> str:
    result = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value.lower()).strip("-")
    return result[:96] or "source"


def _read_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            document = archive.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile) as error:
        raise DomainRuleError("DOCUMENT_PARSE_FAILED", "DOCX 文档结构无效") from error
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as error:
        raise DomainRuleError("DOCUMENT_PARSE_FAILED", "DOCX XML 无效") from error
    paragraphs: list[str] = []
    for paragraph in root.iter():
        if paragraph.tag.endswith("}p"):
            text = "".join(node.text or "" for node in paragraph.iter() if node.tag.endswith("}t"))
            if text.strip():
                paragraphs.append(text.strip())
    return "\n\n".join(paragraphs)


def _read_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return _read_docx(path)
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DomainRuleError("DOCUMENT_DECODE_FAILED", "TXT/Markdown 不是支持的本地文本编码")


class DocumentImportService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.media = MediaService(database, settings)

    def import_document(self, project_id: str, source_path: str | Path, actor: str = "local-user") -> dict[str, Any]:
        source = Path(source_path)
        if source.suffix.lower() not in DOCUMENT_EXTENSIONS:
            raise DomainRuleError("UNSUPPORTED_DOCUMENT_TYPE", "剧本文档仅支持 TXT、Markdown、DOCX")
        try:
            text = _read_text(source.resolve(strict=True))
        except OSError as error:
            raise DomainRuleError("SOURCE_NOT_FOUND", "导入源文件不存在或无法读取") from error
        # Freeze a platform-independent logical script representation. Without
        # this, Windows newline translation can desynchronize the authority hash.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        media = self.media.import_file(project_id, source, purpose="SCRIPT_SOURCE", owner_type="PROJECT", actor=actor)
        digest = media["sha256"]
        source_document_id = str(uuid.uuid4())
        source_version_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        now = _utc_now()
        root = self.media._project_root(project_id)
        text_rel = Path("00_admin") / "imports" / f"{digest}.extracted.txt"
        text_path = root / text_rel
        partial = text_path.with_suffix(".partial.txt")
        partial.write_text(text, encoding="utf-8", newline="")
        partial.replace(text_path)
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
        preview = {"character_count": len(text), "paragraph_count": len(paragraphs), "paragraphs": paragraphs[:20], "requires_llm_confirmation": True}
        source_code = f"{_slug(source.stem)}-{digest[:10]}"
        mime = media.get("mime_type", "application/octet-stream")
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO source_documents (id, project_id, code, title, source_kind, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, 'SCRIPT', ?, ?, ?, 1, 'v2')",
                (source_document_id, project_id, source_code, source.stem[:200], now, now, actor),
            )
            connection.execute(
                """INSERT INTO source_document_versions
                (id, source_document_id, version_no, rel_path, source_name, mime_type, byte_size, sha256, text_sha256,
                 extracted_text_rel, parse_status, metadata_json, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, 'PARSED', ?, ?, ?, ?, 1, 'v2')""",
                (
                    source_version_id,
                    source_document_id,
                    media["rel_path"],
                    source.name,
                    mime,
                    media["byte_size"],
                    digest,
                    text_hash,
                    text_rel.as_posix(),
                    _json({"media_version_id": media["media_version_id"], "paragraph_count": len(paragraphs)}),
                    now,
                    now,
                    actor,
                ),
            )
            connection.execute(
                """INSERT INTO import_sessions
                (id, project_id, source_document_version_id, session_kind, status, preview_json, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, 'SCRIPT', 'PREVIEW_READY', ?, ?, ?, ?, 1, 'v2')""",
                (session_id, project_id, source_version_id, _json(preview), now, now, actor),
            )
            connection.execute(
                """INSERT INTO import_session_items
                (id, session_id, item_type, source_start, source_end, payload_json, validation_status, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, 'DOCUMENT_PREVIEW', 0, ?, ?, 'VALID', ?, ?, ?, 1, 'v2')""",
                (str(uuid.uuid4()), session_id, len(text), _json({"paragraph_count": len(paragraphs), "source_sha256": digest}), now, now, actor),
            )
            connection.execute(
                "INSERT INTO fts_search (project_id, subject_type, subject_id, content) VALUES (?, 'SOURCE_DOCUMENT', ?, ?)",
                (project_id, source_document_id, f"{source.stem}\n{text}"),
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', 'SCRIPT_IMPORTED', 'source_document', ?, ?, ?)",
                (
                    actor,
                    source_document_id,
                    "导入剧本文档并建立预览会话",
                    _json({"session_id": session_id, "sha256": digest, "media_version_id": media["media_version_id"]}),
                ),
            )
        return {
            "source_document_id": source_document_id,
            "source_document_version_id": source_version_id,
            "import_session_id": session_id,
            "media_version_id": media["media_version_id"],
            "status": "PREVIEW_READY",
            "preview": preview,
        }

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            session = connection.execute("SELECT * FROM import_sessions WHERE id = ?", (session_id,)).fetchone()
            if session is None:
                raise DomainRuleError("IMPORT_SESSION_NOT_FOUND", "导入会话不存在")
            version = connection.execute("SELECT * FROM source_document_versions WHERE id = ?", (session["source_document_version_id"],)).fetchone()
            items = connection.execute("SELECT * FROM import_session_items WHERE session_id = ? ORDER BY created_at", (session_id,)).fetchall()
        result = dict(session)
        result["preview"] = json.loads(result.pop("preview_json"))
        result["source_document_version"] = dict(version) if version else None
        result["items"] = [dict(item) for item in items]
        return result

    def request_breakdown(self, session_id: str, profile_version_id: str | None) -> dict[str, Any]:
        if not profile_version_id:
            raise DomainRuleError("LOCAL_LLM_PROFILE_REQUIRED", "剧本拆解必须显式选择已发布的本地 LLM Profile")
        from local_drama.application.local_llm import LocalLLMService

        return LocalLLMService(self.database, self.settings).breakdown(session_id, profile_version_id)

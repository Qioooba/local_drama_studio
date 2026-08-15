"""Versioned TXT/Markdown/DOCX source import with non-destructive previews."""

from __future__ import annotations

import hashlib
import hmac
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
        if source.is_symlink():
            raise DomainRuleError("INVALID_SOURCE_FILE", "剧本文档导入不接受 symlink")
        try:
            text = _read_text(source.resolve(strict=True))
        except OSError as error:
            raise DomainRuleError("SOURCE_NOT_FOUND", "导入源文件不存在或无法读取") from error
        # Freeze a platform-independent logical script representation. Without
        # this, Windows newline translation can desynchronize the authority hash.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        media = self.media.import_file(project_id, source, purpose="SCRIPT_SOURCE", owner_type="PROJECT", actor=actor)
        if media.get("duplicate"):
            media = {**self.media.get_version(str(media["media_version_id"])), **media}
        digest = media["sha256"]
        with self.database.connect() as connection:
            existing_version = connection.execute(
                """SELECT sd.id AS source_document_id, sdv.* FROM source_documents sd
                JOIN source_document_versions sdv ON sdv.source_document_id=sd.id
                WHERE sd.project_id=? AND sdv.sha256=? ORDER BY sdv.created_at DESC LIMIT 1""",
                (project_id, digest),
            ).fetchone()
            reusable_session = connection.execute(
                "SELECT id FROM import_sessions WHERE source_document_version_id=? AND status IN ('PREVIEW_READY','COMMITTED') ORDER BY created_at DESC LIMIT 1",
                (existing_version["id"],),
            ).fetchone() if existing_version is not None else None
        if reusable_session is not None:
            assert existing_version is not None
            session = self.get_session(str(reusable_session["id"]))
            return {
                "source_document_id": str(existing_version["source_document_id"]),
                "source_document_version_id": str(existing_version["id"]),
                "import_session_id": str(reusable_session["id"]),
                "media_version_id": str(media["media_version_id"]),
                "status": session["status"],
                "preview": session["preview"],
                "preview_hash": session["preview_hash"],
                "reused": True,
            }
        source_document_id = str(existing_version["source_document_id"]) if existing_version is not None else str(uuid.uuid4())
        source_version_id = str(existing_version["id"]) if existing_version is not None else str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        now = _utc_now()
        root = self.media._project_root(project_id)
        text_rel = Path("00_admin") / "imports" / f"{digest}.extracted.txt"
        text_path = root / text_rel
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if existing_version is not None:
            if not text_path.is_file() or hashlib.sha256(text_path.read_bytes()).hexdigest() != text_hash:
                raise DomainRuleError("IMPORT_EXTRACTED_TEXT_CHANGED", "已有解析文本缺失或 hash 已变化，禁止覆盖母本")
        else:
            partial = text_path.with_suffix(".partial.txt")
            partial.write_text(text, encoding="utf-8", newline="")
            partial.replace(text_path)
        paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
        preview = {"character_count": len(text), "paragraph_count": len(paragraphs), "paragraphs": paragraphs[:20], "requires_llm_confirmation": True}
        source_code = f"{_slug(source.stem)}-{digest[:10]}"
        mime = media.get("mime_type", "application/octet-stream")
        with self.database.transaction() as connection:
            if existing_version is None:
                connection.execute(
                    "INSERT INTO source_documents (id, project_id, code, title, source_kind, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, 'SCRIPT', ?, ?, ?, 1, 'v2')",
                    (source_document_id, project_id, source_code, source.stem[:200], now, now, actor),
                )
                connection.execute(
                    """INSERT INTO source_document_versions
                    (id, source_document_id, version_no, rel_path, source_name, mime_type, byte_size, sha256, text_sha256,
                     extracted_text_rel, parse_status, metadata_json, created_at, updated_at, created_by, revision, schema_version)
                    VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, 'PARSED', ?, ?, ?, ?, 1, 'v2')""",
                    (source_version_id, source_document_id, media["rel_path"], source.name, mime, media["byte_size"], digest, text_hash, text_rel.as_posix(), _json({"media_version_id": media["media_version_id"], "paragraph_count": len(paragraphs)}), now, now, actor),
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
            if existing_version is None:
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
        result = {
            "source_document_id": source_document_id,
            "source_document_version_id": source_version_id,
            "import_session_id": session_id,
            "media_version_id": media["media_version_id"],
            "status": "PREVIEW_READY",
            "preview": preview,
        }
        result["preview_hash"] = self.get_session(session_id)["preview_hash"]
        return result

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
        result["items"] = [{**dict(item), "payload": json.loads(item["payload_json"])} for item in items]
        result["preview_hash"] = hashlib.sha256(
            _json(
                {
                    "source_document_version_id": result["source_document_version_id"],
                    "preview": result["preview"],
                    "items": [
                        {
                            "id": item["id"],
                            "item_type": item["item_type"],
                            "source_start": item["source_start"],
                            "source_end": item["source_end"],
                            "payload": item["payload"],
                            "validation_status": item["validation_status"],
                        }
                        for item in result["items"]
                    ],
                }
            ).encode("utf-8")
        ).hexdigest()
        result["validation"] = {
            "valid": all(item["validation_status"] == "VALID" for item in result["items"]),
            "issue_count": sum(item["validation_status"] != "VALID" for item in result["items"]),
        }
        return result

    def get_issues(self, session_id: str) -> list[dict[str, Any]]:
        session = self.get_session(session_id)
        return [item for item in session["items"] if item["validation_status"] != "VALID"]

    def commit(self, session_id: str, expected_preview_hash: str, actor: str = "local-user") -> dict[str, Any]:
        session = self.get_session(session_id)
        if not hmac.compare_digest(str(session["preview_hash"]), expected_preview_hash):
            raise DomainRuleError("IMPORT_PREVIEW_STALE", "导入预览已变化，请重新检查后提交")
        if session["status"] == "COMMITTED":
            return {**session, "idempotent": True, "source_preserved": True}
        if session["status"] != "PREVIEW_READY":
            raise DomainRuleError("IMPORT_SESSION_NOT_READY", "只有校验通过的 PREVIEW_READY 导入会话可提交")
        if not session["validation"]["valid"]:
            raise DomainRuleError("IMPORT_VALIDATION_FAILED", "导入预览仍有校验问题", {"issues": self.get_issues(session_id)})
        version = session["source_document_version"]
        if not version or version["parse_status"] != "PARSED":
            raise DomainRuleError("IMPORT_SOURCE_NOT_PARSED", "源文档版本尚未完成解析")
        metadata = json.loads(version["metadata_json"])
        self.media.verify_content_integrity(str(metadata["media_version_id"]))
        project_root = self.media._project_root(str(session["project_id"]))
        extracted_text = (project_root / str(version["extracted_text_rel"])).resolve()
        if not extracted_text.is_relative_to(project_root) or not extracted_text.is_file():
            raise DomainRuleError("IMPORT_EXTRACTED_TEXT_MISSING", "解析后的不可变文本缺失")
        if hashlib.sha256(extracted_text.read_bytes()).hexdigest() != version["text_sha256"]:
            raise DomainRuleError("IMPORT_EXTRACTED_TEXT_CHANGED", "解析后的不可变文本 hash 已变化")
        now = _utc_now()
        snapshot = {
            "source_document_version_id": session["source_document_version_id"],
            "source_sha256": version["sha256"],
            "text_sha256": version["text_sha256"],
            "preview_hash": session["preview_hash"],
            "validated_item_ids": [item["id"] for item in session["items"]],
        }
        idempotent_race = False
        with self.database.transaction() as connection:
            updated = connection.execute(
                "UPDATE import_sessions SET status='COMMITTED', updated_at=?, revision=revision+1 WHERE id=? AND status='PREVIEW_READY' AND revision=?",
                (now, session_id, session["revision"]),
            )
            if updated.rowcount != 1:
                current = connection.execute("SELECT status FROM import_sessions WHERE id=?", (session_id,)).fetchone()
                if current is None or current["status"] != "COMMITTED":
                    raise DomainRuleError("IMPORT_COMMIT_CONFLICT", "导入会话提交冲突，请重新读取")
                idempotent_race = True
            if not idempotent_race:
                connection.execute(
                    "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', 'IMPORT_SESSION_COMMITTED', 'import_session', ?, ?, ?)",
                    (actor, session_id, "确认剧本文档解析预览", _json(snapshot)),
                )
        committed = self.get_session(session_id)
        return {**committed, "idempotent": idempotent_race, "source_preserved": True, "commit_snapshot": snapshot}

    def request_breakdown(self, session_id: str, profile_version_id: str | None) -> dict[str, Any]:
        if not profile_version_id:
            raise DomainRuleError("LOCAL_LLM_PROFILE_REQUIRED", "剧本拆解必须显式选择已发布的本地 LLM Profile")
        from local_drama.application.local_llm import LocalLLMService

        return LocalLLMService(self.database, self.settings).breakdown(session_id, profile_version_id)

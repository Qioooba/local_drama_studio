"""Versioned novel/script imports with non-destructive text previews."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree

from pypdf import PdfReader

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.filesystem.path_policy import canonical_relative_path, controlled_path

from .local_artifacts import local_artifact_reference
from .media import DOCUMENT_EXTENSIONS, MediaService
from .source_text import looks_like_source_heading, source_chapters, source_paragraphs

PREVIEW_PARAGRAPH_LIMIT = 20
PREVIEW_PARAGRAPH_CHARACTER_LIMIT = 1_000
PREVIEW_TOTAL_CHARACTER_LIMIT = 12_000
PASSAGE_CHARACTER_LIMIT = 8_000
SOURCE_STRUCTURE_VERSION = 2
EPUB_MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024


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


def _read_pdf(path: Path) -> str:
    try:
        reader = PdfReader(path)
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as error:
        raise DomainRuleError("DOCUMENT_PARSE_FAILED", "PDF 文档无法解析或已加密") from error
    text = "\n\n".join(page for page in pages if page)
    if not text.strip():
        raise DomainRuleError("DOCUMENT_TEXT_EMPTY", "PDF 没有可提取文字；扫描版请先完成 OCR")
    return text


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _epub_document_text(payload: bytes) -> str:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return ""
    blocks: list[str] = []
    for node in root.iter():
        if _xml_local_name(node.tag) not in {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote"}:
            continue
        text = "".join(node.itertext())
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            blocks.append(text)
    return "\n\n".join(blocks)


def _read_epub(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
            rootfile = next(
                node.attrib["full-path"] for node in container.iter() if _xml_local_name(node.tag) == "rootfile"
            )
            package = ElementTree.fromstring(archive.read(rootfile))
            base = Path(rootfile).parent
            manifest = {
                str(node.attrib.get("id")): str(node.attrib.get("href"))
                for node in package.iter()
                if _xml_local_name(node.tag) == "item" and node.attrib.get("id") and node.attrib.get("href")
            }
            ordered = [
                unquote(manifest[str(node.attrib.get("idref"))].split("#", 1)[0].split("?", 1)[0])
                for node in package.iter()
                if _xml_local_name(node.tag) == "itemref" and str(node.attrib.get("idref")) in manifest
            ]
            selected_names = [(base / href).as_posix() for href in ordered]
            expanded_size = sum(archive.getinfo(name).file_size for name in selected_names)
            if expanded_size > EPUB_MAX_UNCOMPRESSED_BYTES:
                raise DomainRuleError("DOCUMENT_TOO_LARGE_EXPANDED", "EPUB 解压后的正文超过 100 MB，请拆分后导入")
            chapters = [_epub_document_text(archive.read(name)) for name in selected_names]
    except (OSError, KeyError, StopIteration, zipfile.BadZipFile, ElementTree.ParseError) as error:
        raise DomainRuleError("DOCUMENT_PARSE_FAILED", "EPUB 文档结构无效") from error
    text = "\n\n".join(chapter for chapter in chapters if chapter.strip())
    if not text.strip():
        raise DomainRuleError("DOCUMENT_TEXT_EMPTY", "EPUB 没有可提取的正文")
    return text


def _read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _read_docx(path)
    if suffix == ".pdf":
        return _read_pdf(path)
    if suffix == ".epub":
        return _read_epub(path)
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
            raise DomainRuleError("UNSUPPORTED_DOCUMENT_TYPE", "原稿仅支持 TXT、Markdown、DOCX、PDF、EPUB")
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
        stored_source = self._stored_source_reference(project_id, media, original_filename=source.name)
        digest = media["sha256"]
        with self.database.connect() as connection:
            existing_version = connection.execute(
                """SELECT sd.id AS source_document_id, sdv.* FROM source_documents sd
                JOIN source_document_versions sdv ON sdv.source_document_id=sd.id
                WHERE sd.project_id=? AND sdv.sha256=? ORDER BY sdv.created_at DESC LIMIT 1""",
                (project_id, digest),
            ).fetchone()
            reusable_session = connection.execute(
                "SELECT id, preview_json FROM import_sessions WHERE source_document_version_id=? AND status IN ('PREVIEW_READY','COMMITTED') ORDER BY created_at DESC LIMIT 1",
                (existing_version["id"],),
            ).fetchone() if existing_version is not None else None
            if reusable_session is not None:
                try:
                    reusable_preview = json.loads(str(reusable_session["preview_json"] or "{}"))
                except json.JSONDecodeError:
                    reusable_preview = {}
                if reusable_preview.get("source_structure_version") != SOURCE_STRUCTURE_VERSION:
                    reusable_session = None
        if reusable_session is not None:
            assert existing_version is not None
            session = self.get_session(str(reusable_session["id"]))
            index_status = self._replace_search_index(project_id, str(existing_version["source_document_id"]), source.stem, text, actor)
            return {
                "source_document_id": str(existing_version["source_document_id"]),
                "source_document_version_id": str(existing_version["id"]),
                "import_session_id": str(reusable_session["id"]),
                "media_version_id": str(media["media_version_id"]),
                "stored_source": stored_source,
                "status": session["status"],
                "preview": session["preview"],
                "preview_hash": session["preview_hash"],
                "reused": True,
                "index_status": index_status,
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
        paragraph_records = source_paragraphs(text)
        paragraphs = [paragraph.text for paragraph in paragraph_records]
        chapters = source_chapters(paragraph_records)
        preview_paragraphs: list[str] = []
        remaining_preview_characters = PREVIEW_TOTAL_CHARACTER_LIMIT
        for paragraph in paragraphs[:PREVIEW_PARAGRAPH_LIMIT]:
            if remaining_preview_characters <= 0:
                break
            bounded = paragraph[: min(PREVIEW_PARAGRAPH_CHARACTER_LIMIT, remaining_preview_characters)]
            preview_paragraphs.append(bounded)
            remaining_preview_characters -= len(bounded)
        preview = {
            "source_structure_version": SOURCE_STRUCTURE_VERSION,
            "character_count": len(text),
            "paragraph_count": len(paragraphs),
            "paragraphs": preview_paragraphs,
            "chapters": chapters,
            "preview_character_limit": PREVIEW_TOTAL_CHARACTER_LIMIT,
            "preview_truncated": len(paragraphs) > len(preview_paragraphs)
            or any(len(original) > len(shown) for original, shown in zip(paragraphs, preview_paragraphs, strict=False)),
            "offset_unit": "UNICODE_CODEPOINT",
            "requires_llm_confirmation": True,
        }
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
                    (source_version_id, source_document_id, media["rel_path"], source.name, mime, media["byte_size"], digest, text_hash, text_rel.as_posix(), _json({"media_version_id": media["media_version_id"], "paragraph_count": len(paragraphs), "character_count": len(text), "offset_unit": "UNICODE_CODEPOINT"}), now, now, actor),
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
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', 'SCRIPT_IMPORTED', 'source_document', ?, ?, ?)",
                (
                    actor,
                    source_document_id,
                    "导入剧本文档并建立预览会话",
                    _json({"session_id": session_id, "sha256": digest, "media_version_id": media["media_version_id"]}),
                ),
            )
        # FTS is a derived read model. Index its large payload outside the
        # authoritative source/session transaction, and surface failure so a
        # re-import or explicit search rebuild can retry without losing source.
        index_status = self._replace_search_index(project_id, source_document_id, source.stem, text, actor)
        result = {
            "source_document_id": source_document_id,
            "source_document_version_id": source_version_id,
            "import_session_id": session_id,
            "media_version_id": media["media_version_id"],
            "stored_source": stored_source,
            "status": "PREVIEW_READY",
            "preview": preview,
            "index_status": index_status,
        }
        result["preview_hash"] = self.get_session(session_id)["preview_hash"]
        return result

    def latest_for_project(self, project_id: str) -> dict[str, Any] | None:
        """Restore the latest durable script-import workflow for a project."""
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT id FROM import_sessions
                   WHERE project_id=? AND session_kind='SCRIPT'
                     AND status IN ('PREVIEW_READY','COMMITTED')
                   ORDER BY updated_at DESC,created_at DESC,id DESC LIMIT 1""",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        session = self.get_session(str(row["id"]))
        version = session.get("source_document_version")
        if not isinstance(version, dict):
            raise DomainRuleError("IMPORT_SOURCE_VERSION_MISSING", "最近导入会话缺少源文档版本，无法恢复")
        try:
            metadata = json.loads(str(version.get("metadata_json") or "{}"))
        except json.JSONDecodeError as error:
            raise DomainRuleError("IMPORT_SOURCE_METADATA_INVALID", "最近导入会话的源文档元数据无效") from error
        media_version_id = metadata.get("media_version_id") if isinstance(metadata, dict) else None
        if not isinstance(media_version_id, str) or not media_version_id:
            raise DomainRuleError("IMPORT_MEDIA_VERSION_MISSING", "最近导入会话缺少受控原文件版本")
        media = self.media.get_version(media_version_id)
        source_name = str(version.get("source_name") or "已导入原稿")
        selected_range = next(
            (
                item.get("payload") for item in reversed(session["items"])
                if item.get("item_type") == "SOURCE_BODY_RANGE" and isinstance(item.get("payload"), dict)
            ),
            None,
        )
        with self.database.connect() as connection:
            indexed = connection.execute(
                """SELECT 1 FROM fts_search
                   WHERE project_id=? AND subject_type='SOURCE_DOCUMENT' AND subject_id=? LIMIT 1""",
                (project_id, str(version["source_document_id"])),
            ).fetchone()
        return {
            "import": {
                "source_document_id": str(version["source_document_id"]),
                "source_document_version_id": str(session["source_document_version_id"]),
                "import_session_id": str(session["id"]),
                "media_version_id": media_version_id,
                "stored_source": self._stored_source_reference(
                    project_id,
                    {**media, "media_version_id": media_version_id},
                    original_filename=source_name,
                ),
                "status": str(session["status"]),
                "preview_hash": str(session["preview_hash"]),
                "preview": session["preview"],
                "index_status": "READY" if indexed is not None else "FAILED_RETRYABLE",
                "reused": True,
            },
            "source_name": source_name,
            "selected_range": selected_range,
        }

    def _stored_source_reference(
        self,
        project_id: str,
        media: dict[str, Any],
        *,
        original_filename: str,
    ) -> dict[str, str]:
        """Return user-facing provenance for the immutable server copy."""
        project_root = self.media._project_root(project_id)
        rel_path = canonical_relative_path(
            str(media.get("rel_path") or ""), code="IMPORT_SOURCE_PATH_INVALID",
        )
        stored_path = controlled_path(
            project_root,
            rel_path,
            must_exist=True,
            require_file=True,
            code="IMPORT_SOURCE_PATH_MISSING",
        )
        media_version_id = str(media["media_version_id"])
        return local_artifact_reference(
            root=project_root,
            path=stored_path,
            scope="PROJECT",
            kind="FILE",
            display_name=original_filename,
            download_url=f"/api/v1/media-versions/{media_version_id}/content",
            download_filename=original_filename,
            error_code="IMPORT_SOURCE_PATH_INVALID",
        )

    def _replace_search_index(self, project_id: str, source_document_id: str, title: str, text: str, actor: str) -> str:
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    "DELETE FROM fts_search WHERE project_id=? AND subject_type='SOURCE_DOCUMENT' AND subject_id=?",
                    (project_id, source_document_id),
                )
                connection.execute(
                    "INSERT INTO fts_search (project_id,subject_type,subject_id,content) VALUES (?,'SOURCE_DOCUMENT',?,?)",
                    (project_id, source_document_id, f"{title}\n{text}"),
                )
            return "READY"
        except sqlite3.DatabaseError as error:
            # The source/version/session transaction has already committed.
            # Record a bounded, redacted signal when SQLite remains writable;
            # never delete or rewrite the verified source to hide index failure.
            try:
                with self.database.transaction() as connection:
                    connection.execute(
                        """INSERT INTO audit_events
                        (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                        VALUES (?,'producer','SOURCE_SEARCH_INDEX_FAILED','source_document',?, '源文本搜索索引失败，可重试重建',?)""",
                        (actor, source_document_id, _json({"error_type": type(error).__name__})),
                    )
            except sqlite3.DatabaseError:
                pass
            return "FAILED_RETRYABLE"

    def get_passage(self, source_document_version_id: str, start: int, end: int) -> dict[str, Any]:
        """Return an exact bounded slice in Unicode-codepoint offsets.

        This is the same unit produced by LocalLLM source quote validation and
        stored in episode_scene_ranges. UTF-8 byte offsets are never accepted.
        """
        if start < 0 or end <= start:
            raise DomainRuleError("SOURCE_PASSAGE_RANGE_INVALID", "source passage 起止字符范围无效")
        if end - start > PASSAGE_CHARACTER_LIMIT:
            raise DomainRuleError(
                "SOURCE_PASSAGE_TOO_LARGE", f"source passage 单次最多返回 {PASSAGE_CHARACTER_LIMIT} 个 Unicode 字符",
                {"maximum_character_count": PASSAGE_CHARACTER_LIMIT},
            )
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT sdv.id,sdv.extracted_text_rel,sdv.text_sha256,sdv.metadata_json,sd.project_id
                FROM source_document_versions sdv JOIN source_documents sd ON sd.id=sdv.source_document_id
                WHERE sdv.id=?""", (source_document_version_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("SOURCE_DOCUMENT_VERSION_NOT_FOUND", "源文档版本不存在")
        project_root = self.media._project_root(str(row["project_id"]))
        path = controlled_path(
            project_root,
            str(row["extracted_text_rel"]),
            must_exist=True,
            require_file=True,
            code="IMPORT_EXTRACTED_TEXT_MISSING",
        )
        passage, actual_end, has_more = self._read_passage(path, start, end)
        metadata = json.loads(str(row["metadata_json"] or "{}"))
        total = metadata.get("character_count")
        return {
            "source_document_version_id": source_document_version_id,
            "source_start": start,
            "source_end": actual_end,
            "requested_end": end,
            "offset_unit": "UNICODE_CODEPOINT",
            "text": passage,
            "text_sha256": hashlib.sha256(passage.encode("utf-8")).hexdigest(),
            "source_text_sha256": str(row["text_sha256"]),
            "total_character_count": int(total) if isinstance(total, int) else None,
            "has_more": has_more,
            "maximum_character_count": PASSAGE_CHARACTER_LIMIT,
            "read_only": True,
        }

    @staticmethod
    def _read_passage(path: Path, start: int, end: int) -> tuple[str, int, bool]:
        cursor = 0
        parts: list[str] = []
        has_more = False
        with path.open("r", encoding="utf-8", newline="") as handle:
            while cursor < end + 1:
                chunk = handle.read(min(8_192, end + 1 - cursor))
                if not chunk:
                    break
                chunk_end = cursor + len(chunk)
                overlap_start = max(start, cursor)
                overlap_end = min(end, chunk_end)
                if overlap_start < overlap_end:
                    parts.append(chunk[overlap_start - cursor : overlap_end - cursor])
                if chunk_end > end:
                    has_more = True
                cursor = chunk_end
        passage = "".join(parts)
        return passage, start + len(passage), has_more

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
                        if item["item_type"] == "DOCUMENT_PREVIEW"
                    ],
                }
            ).encode("utf-8")
        ).hexdigest()
        result["validation"] = {
            "valid": all(item["validation_status"] == "VALID" for item in result["items"]),
            "issue_count": sum(item["validation_status"] != "VALID" for item in result["items"]),
        }
        return result

    def get_paragraphs(self, session_id: str, *, start: int = 1, limit: int = 40) -> dict[str, Any]:
        """Page through the immutable server-parsed paragraph authority."""
        session = self.get_session(session_id)
        version = session.get("source_document_version")
        if not version or version.get("parse_status") != "PARSED":
            raise DomainRuleError("IMPORT_SOURCE_NOT_PARSED", "源文档版本尚未完成解析")
        project_root = self.media._project_root(str(session["project_id"]))
        extracted_text = controlled_path(
            project_root,
            str(version["extracted_text_rel"]),
            must_exist=True,
            require_file=True,
            code="IMPORT_EXTRACTED_TEXT_MISSING",
        )
        text = extracted_text.read_text(encoding="utf-8")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != str(version["text_sha256"]):
            raise DomainRuleError("IMPORT_EXTRACTED_TEXT_CHANGED", "解析后的不可变文本 hash 已变化")
        paragraphs = source_paragraphs(text)
        if not paragraphs:
            raise DomainRuleError("IMPORT_SOURCE_EMPTY", "解析后的正文没有可用段落")
        if start > len(paragraphs):
            raise DomainRuleError(
                "IMPORT_PARAGRAPH_PAGE_OUT_OF_RANGE",
                f"正文页起始段不能超过 {len(paragraphs)}",
                {"total_paragraph_count": len(paragraphs)},
            )
        page = paragraphs[start - 1 : start - 1 + limit]
        return {
            "session_id": session_id,
            "source_document_version_id": str(session["source_document_version_id"]),
            "start_paragraph": page[0].number,
            "end_paragraph": page[-1].number,
            "total_paragraph_count": len(paragraphs),
            "items": [
                {
                    "number": paragraph.number,
                    "text": paragraph.text,
                    "source_start": paragraph.start,
                    "source_end": paragraph.end,
                    "is_heading": looks_like_source_heading(paragraph.text),
                }
                for paragraph in page
            ],
            "chapters": session["preview"].get("chapters", []),
            "has_previous": page[0].number > 1,
            "has_more": page[-1].number < len(paragraphs),
            "read_only": True,
        }

    def get_issues(self, session_id: str) -> list[dict[str, Any]]:
        session = self.get_session(session_id)
        return [item for item in session["items"] if item["validation_status"] != "VALID"]

    def commit(
        self,
        session_id: str,
        expected_preview_hash: str,
        actor: str = "local-user",
        *,
        source_paragraph_start: int | None = None,
        source_paragraph_end: int | None = None,
    ) -> dict[str, Any]:
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
        extracted_text = controlled_path(
            project_root,
            str(version["extracted_text_rel"]),
            must_exist=True,
            require_file=True,
            code="IMPORT_EXTRACTED_TEXT_MISSING",
        )
        if hashlib.sha256(extracted_text.read_bytes()).hexdigest() != version["text_sha256"]:
            raise DomainRuleError("IMPORT_EXTRACTED_TEXT_CHANGED", "解析后的不可变文本 hash 已变化")
        paragraphs = source_paragraphs(extracted_text.read_text(encoding="utf-8"))
        if not paragraphs:
            raise DomainRuleError("IMPORT_SOURCE_EMPTY", "解析后的正文没有可用段落")
        if (source_paragraph_start is None) != (source_paragraph_end is None):
            raise DomainRuleError("IMPORT_BODY_RANGE_INCOMPLETE", "正文范围必须同时提供起始段和结束段")
        selection_mode = "EXPLICIT" if source_paragraph_start is not None else "FULL_DOCUMENT_DEFAULT"
        range_start = source_paragraph_start if source_paragraph_start is not None else 1
        range_end = source_paragraph_end if source_paragraph_end is not None else len(paragraphs)
        if range_start < 1 or range_end < range_start or range_end > len(paragraphs):
            raise DomainRuleError(
                "IMPORT_BODY_RANGE_INVALID",
                f"正文范围必须位于 1–{len(paragraphs)} 段内，且结束段不得早于起始段",
                {"total_paragraph_count": len(paragraphs)},
            )
        body_range = {
            "source_paragraph_start": range_start,
            "source_paragraph_end": range_end,
            "source_paragraph_count": range_end - range_start + 1,
            "selection_mode": selection_mode,
        }
        now = _utc_now()
        snapshot = {
            "source_document_version_id": session["source_document_version_id"],
            "source_sha256": version["sha256"],
            "text_sha256": version["text_sha256"],
            "preview_hash": session["preview_hash"],
            "validated_item_ids": [item["id"] for item in session["items"]],
            "body_range": body_range,
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
                    """INSERT INTO import_session_items
                    (id, session_id, item_type, source_start, source_end, payload_json, validation_status, created_at, updated_at, created_by, revision, schema_version)
                    VALUES (?, ?, 'SOURCE_BODY_RANGE', ?, ?, ?, 'VALID', ?, ?, ?, 1, 'v2')""",
                    (
                        str(uuid.uuid4()),
                        session_id,
                        paragraphs[range_start - 1].start,
                        paragraphs[range_end - 1].end,
                        _json(body_range),
                        now,
                        now,
                        actor,
                    ),
                )
                connection.execute(
                    "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', 'IMPORT_SESSION_COMMITTED', 'import_session', ?, ?, ?)",
                    (actor, session_id, "确认剧本文档解析预览", _json(snapshot)),
                )
        committed = self.get_session(session_id)
        return {**committed, "idempotent": idempotent_race, "source_preserved": True, "commit_snapshot": snapshot}

    def request_breakdown(
        self,
        session_id: str,
        profile_version_id: str | None,
        idempotency_key: str,
        *,
        target_episode_id: str | None = None,
        source_paragraph_start: int | None = None,
        source_paragraph_end: int | None = None,
    ) -> dict[str, Any]:
        """Queue a durable local-LLM Job; never execute the model in the API."""
        from local_drama.application.local_llm import LocalLLMService

        return LocalLLMService(self.database, self.settings).enqueue_breakdown(
            session_id,
            profile_version_id,
            idempotency_key,
            target_episode_id=target_episode_id,
            source_paragraph_start=source_paragraph_start,
            source_paragraph_end=source_paragraph_end,
        )

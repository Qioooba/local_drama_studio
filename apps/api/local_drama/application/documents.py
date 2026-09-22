"""Versioned novel/script imports with non-destructive text previews."""

from __future__ import annotations

import hashlib
import hmac
import json
import posixpath
import re
import sqlite3
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

from pypdf import PdfReader

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.source_text import ParagraphLayoutHint, SourceParagraph
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.filesystem.path_policy import canonical_relative_path, controlled_path

from .local_artifacts import local_artifact_reference
from .media import DOCUMENT_EXTENSIONS, MediaService
from .source_text import (
    LEGACY_PARSER_VERSION,
    LEGACY_SOURCE_STRUCTURE_VERSION,
    SOURCE_PARSER_VERSION,
    SOURCE_STRUCTURE_VERSION,
    looks_like_source_heading,
    recommends_newline_paragraphs,
    resolve_paragraph_layout,
    source_chapters,
    source_paragraphs,
)

PREVIEW_PARAGRAPH_LIMIT = 20
PREVIEW_PARAGRAPH_CHARACTER_LIMIT = 1_000
PREVIEW_TOTAL_CHARACTER_LIMIT = 12_000
PASSAGE_CHARACTER_LIMIT = 8_000
EPUB_MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024

_EPUB_TRAVERSAL_LIMIT = 20_000
_URI_PERCENT_DECODE_LIMIT = 4
_SCOPE_SELECTION_MODES = ("EXPLICIT", "FULL_DOCUMENT_DEFAULT")

_EXTRACTED_TEXT_FILENAME = re.compile(r"^(?P<digest>[0-9a-f]{64})\.(?P<version>[0-9a-f]{12})\.extracted\.txt$")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: object) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _slug(value: str) -> str:
    result = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value.lower()).strip("-")
    return result[:96] or "source"


def _parse_version(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _effective_parse_versions(
    metadata: dict[str, Any] | None,
    preview: dict[str, Any] | None,
) -> tuple[int | None, int | None]:
    """Return the parse/structure versions that produced an existing text.

    Rows written before the parse-version columns existed only carry the values
    inside ``metadata_json``/``preview_json``; a row with neither is a legacy
    parse produced by the version-2 structure grammar.
    """
    metadata = metadata or {}
    preview = preview or {}
    parser_version = _parse_version(metadata.get("parser_version"))
    structure_version = _parse_version(metadata.get("structure_version"))
    preview_structure = _parse_version(preview.get("source_structure_version"))
    if structure_version is None:
        structure_version = preview_structure
    if preview_structure is not None and structure_version != preview_structure:
        # The preview records the structure index the session was rendered
        # from; a mismatch means the preview must be rebuilt.
        structure_version = None
    if parser_version is None and not metadata and not preview:
        return SOURCE_PARSER_VERSION, LEGACY_SOURCE_STRUCTURE_VERSION
    return parser_version, structure_version


def _extraction_path_identity(extracted_text_rel: str) -> str | None:
    """Return the ``<parser/structure/layout>`` identity of an extracted path."""
    name = PurePosixPath(str(extracted_text_rel or "")).name
    match = _EXTRACTED_TEXT_FILENAME.match(name)
    return match.group("version") if match else None


def _extraction_identity(parser_version: int, structure_version: int, paragraph_layout: str) -> str:
    raw = f"{parser_version}:{structure_version}:{paragraph_layout}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def _docx_blocks(container: ElementTree.Element) -> list[str]:
    """Collect DOCX block text once, in logical reading order.

    Consecutive paragraphs/cells join with a newline and each table row becomes
    its own block, so ``w:tab``, ``w:br``/``w:cr`` and cell boundaries survive
    in reading order.  A paragraph nested inside another paragraph (text-box and
    footnote content) is flattened into its parent instead of being collected
    twice, so the same characters are never emitted more than once.
    """
    blocks: list[str] = []
    for child in container:
        name = _xml_local_name(child.tag)
        if name == "p":
            nested = "".join(_docx_blocks(child)).strip("\n")
            if nested:
                blocks.append(nested)
            continue
        if name in {"tc", "tr", "tbl", "sdtContent", "txbxContent", "footnote", "endnote", "hdr", "ftr"}:
            blocks.extend(_docx_blocks(child))
            continue
        if name == "t":
            text = child.text or ""
            if text:
                blocks.append(text)
            continue
        if name == "tab":
            blocks.append("\t")
            continue
        if name in {"br", "cr"}:
            blocks.append("\n")
            continue
        blocks.extend(_docx_blocks(child))
    return blocks


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
    body = next((node for node in root.iter() if _xml_local_name(node.tag) == "body"), root)
    return "\n\n".join(block.strip("\n") for block in _docx_blocks(body) if block.strip())


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


def _xml_local_name(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()


_EPUB_BLOCK_NAMES = {
    "address", "article", "aside", "blockquote", "body", "caption", "center", "dd", "div", "dl", "dt",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
    "main", "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}


def _epub_text_blocks(root: ElementTree.Element) -> list[str]:
    """Return each source text node exactly once, in document order.

    Parent blocks that contain extractable child blocks are not re-emitted as a
    whole: their own direct text becomes its own block and their children are
    traversed.  Genuinely repeated prose is preserved because nothing is ever
    de-duplicated globally.
    """
    blocks: list[str] = []
    stack: list[ElementTree.Element] = [root]
    inline_text: list[str] = []
    visited = 0

    def flush() -> None:
        if not inline_text:
            return
        collapsed = re.sub(r"\s+", " ", "".join(inline_text)).strip()
        inline_text.clear()
        if collapsed:
            blocks.append(collapsed)

    while stack:
        node = stack.pop()
        visited += 1
        if visited > _EPUB_TRAVERSAL_LIMIT:
            break
        name = _xml_local_name(node.tag)
        if name == "style" or name == "script":
            continue
        if name in _EPUB_BLOCK_NAMES:
            # "p" inside "li"/"blockquote"/"div" must not re-emit the parent.
            flush()
            for child in reversed(list(node)):
                if isinstance(child.tag, str):
                    stack.append(child)
            if node.text and node.text.strip():
                inline_text.append(node.text)
            if node.tail and node.tail.strip():
                inline_text.append(node.tail)
            continue
        if node.text:
            inline_text.append(node.text)
        for child in node:
            if isinstance(child.tag, str):
                stack.append(child)
        if node.tail:
            inline_text.append(node.tail)
    flush()
    return blocks


def _epub_document_text(payload: bytes) -> str:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return ""
    return "\n\n".join(_epub_text_blocks(root))


def _percent_decode_uri_path(value: str) -> str:
    """Decode percent escapes until stable, rejecting undecodable leftovers."""
    decoded = value
    for _ in range(_URI_PERCENT_DECODE_LIMIT):
        next_value = unquote(decoded)
        if next_value == decoded:
            return decoded
        decoded = next_value
    if "%" in decoded:
        raise DomainRuleError("DOCUMENT_PARSE_FAILED", "EPUB 清单引用包含无法规范化的转义序列")
    return decoded


def _normalise_epub_reference(base_dir: str, href: str) -> str:
    """Resolve one in-package EPUB href to a normalised archive path.

    Only POSIX URI rules are used: fragment/query are stripped, percent escapes
    are decoded *before* the boundary check so escape sequences cannot smuggle
    ``..`` past it, and any result that leaves the ZIP root, is absolute, or
    points outside the package is rejected.
    """
    reference = str(href or "").strip()
    if not reference:
        raise DomainRuleError("DOCUMENT_PARSE_FAILED", "EPUB 清单存在空引用")
    split = urlsplit(reference)
    if split.scheme or split.netloc:
        raise DomainRuleError("DOCUMENT_PARSE_FAILED", "EPUB 清单引用了外部网络地址")
    if reference.startswith("/") or reference.startswith("\\") or "\\" in reference:
        raise DomainRuleError("DOCUMENT_PARSE_FAILED", "EPUB 清单引用了非包内路径")
    decoded = _percent_decode_uri_path(split.path)
    if not decoded or decoded.startswith("/"):
        raise DomainRuleError("DOCUMENT_PARSE_FAILED", "EPUB 清单引用了非包内路径")
    _reject_epub_root_escape(base_dir, decoded)
    normalised = posixpath.normpath(posixpath.join(base_dir, decoded))
    if normalised in {".", "..", ""} or normalised.startswith("../") or normalised.startswith("/"):
        raise DomainRuleError("DOCUMENT_PARSE_FAILED", "EPUB 清单引用了包外路径")
    return normalised


def _reject_epub_root_escape(base_dir: str, decoded: str) -> None:
    """Reject ``..`` segments that would climb above the package root.

    The check runs on the *decoded* path (percent escapes already expanded), so
    ``%2e%2e`` cannot smuggle a parent segment past it, and it counts how many
    levels the reference actually climbs: more than the directory depth of
    ``base_dir`` means the reference leaves the ZIP root.
    """
    depth = sum(1 for segment in base_dir.split("/") if segment not in {"", "."})
    climb = sum(1 for segment in decoded.split("/") if segment == "..")
    if climb > depth:
        raise DomainRuleError(
            "DOCUMENT_PARSE_FAILED",
            "EPUB 清单引用了包外路径",
            {"reference": decoded, "package_depth": depth, "parent_segments": climb},
        )


def _read_epub(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
            rootfile = next(
                node.attrib["full-path"] for node in container.iter() if _xml_local_name(node.tag) == "rootfile"
            )
            package = ElementTree.fromstring(archive.read(rootfile))
            base_dir = posixpath.dirname(posixpath.normpath(rootfile.replace("\\", "/")))
            manifest = {
                str(node.attrib.get("id")): str(node.attrib.get("href"))
                for node in package.iter()
                if _xml_local_name(node.tag) == "item" and node.attrib.get("id") and node.attrib.get("href")
            }
            ordered = [
                _normalise_epub_reference(base_dir, manifest[str(node.attrib.get("idref"))])
                for node in package.iter()
                if _xml_local_name(node.tag) == "itemref" and str(node.attrib.get("idref")) in manifest
            ]
            if not ordered:
                raise DomainRuleError("DOCUMENT_PARSE_FAILED", "EPUB spine 没有可读取的章节引用")
            expanded_size = sum(archive.getinfo(name).file_size for name in ordered)
            if expanded_size > EPUB_MAX_UNCOMPRESSED_BYTES:
                raise DomainRuleError("DOCUMENT_TOO_LARGE_EXPANDED", "EPUB 解压后的正文超过 100 MB，请拆分后导入")
            chapters = [_epub_document_text(archive.read(name)) for name in ordered]
    except DomainRuleError:
        raise
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


def _validate_parsed_text(text: str, *, paragraph_layout: ParagraphLayoutHint) -> list[SourceParagraph]:
    """Validate the format-parsed text *before* any authoritative record exists."""
    paragraphs = source_paragraphs(text, paragraph_layout)
    if not paragraphs:
        raise DomainRuleError(
            "DOCUMENT_TEXT_EMPTY",
            "文档解析后没有可用的正文段落；仅空白、只有换行或无可提取文字的文件不能导入",
            {"character_count": len(text)},
        )
    return paragraphs


class DocumentImportService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.media = MediaService(database, settings)

    def import_document(
        self,
        project_id: str,
        source_path: str | Path,
        actor: str = "local-user",
        *,
        paragraph_layout: ParagraphLayoutHint | None = None,
    ) -> dict[str, Any]:
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
        requested_layout: ParagraphLayoutHint = paragraph_layout or "AUTO"
        resolved_layout = resolve_paragraph_layout(text, requested_layout)
        # NP04: the format-parsed text must yield paragraphs before the media
        # copy, the extracted text or any ready session is persisted.
        paragraphs = _validate_parsed_text(text, paragraph_layout=resolved_layout)
        chapters = source_chapters(paragraphs, resolved_layout)
        media = self.media.import_file(project_id, source, purpose="SCRIPT_SOURCE", owner_type="PROJECT", actor=actor)
        if media.get("duplicate"):
            media = {**self.media.get_version(str(media["media_version_id"])), **media}
        stored_source = self._stored_source_reference(project_id, media, original_filename=source.name)
        digest = media["sha256"]
        text_hash = _sha256_bytes(text.encode("utf-8"))
        root = self.media._project_root(project_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT sd.id AS source_document_id, sd.code AS source_document_code, sdv.*
                FROM source_documents sd
                JOIN source_document_versions sdv ON sdv.source_document_id=sd.id
                WHERE sd.project_id=? AND sdv.sha256=? ORDER BY sdv.created_at DESC, sdv.version_no DESC""",
                (project_id, digest),
            ).fetchall()
        current_identity = _extraction_identity(SOURCE_PARSER_VERSION, SOURCE_STRUCTURE_VERSION, requested_layout)
        existing_version = next(
            (
                row
                for row in rows
                if _extraction_path_identity(str(row["extracted_text_rel"])) == current_identity
                and str(row["text_sha256"] or "") == text_hash
            ),
            None,
        )
        if existing_version is not None:
            # The raw source and the frozen extracted text are identical: this is
            # the same parse generation, so reuse it instead of minting a version.
            self._replace_extracted_text(root / str(existing_version["extracted_text_rel"]), text)
            return self._reuse_import(
                project_id=project_id,
                media=media,
                stored_source=stored_source,
                existing_version=existing_version,
                text=text,
                actor=actor,
                requested=None,
                requested_layout=requested_layout,
            )
        legacy_reuse = next(
            (
                row
                for row in rows
                if str(row["text_sha256"] or "") == text_hash
                and _parse_version(row["parser_version"]) in {None, LEGACY_PARSER_VERSION}
                and _parse_version(row["structure_version"]) in {None, LEGACY_SOURCE_STRUCTURE_VERSION}
                and self._version_paragraph_layout(dict(row)) == resolved_layout
            ),
            None,
        )
        if legacy_reuse is not None:
            # A pre-parse-generation row already froze exactly this text under
            # the same paragraph layout: adopt it and keep its version id so
            # existing sessions keep working without minting a duplicate.
            try:
                frozen = self._read_verified_extracted_text(legacy_reuse, root)
            except DomainRuleError:
                frozen = ""
            if frozen == text:
                return self._reuse_import(
                    project_id=project_id,
                    media=media,
                    stored_source=stored_source,
                    existing_version=legacy_reuse,
                    text=frozen,
                    actor=actor,
                    requested=None,
                    requested_layout=requested_layout,
                )
        source_document_id = str(rows[0]["source_document_id"]) if rows else str(uuid.uuid4())
        source_code = str(rows[0]["source_document_code"]) if rows else f"{_slug(source.stem)}-{digest[:10]}"
        source_version_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        now = _utc_now()
        text_rel = Path("00_admin") / "imports" / f"{digest}.{current_identity}.extracted.txt"
        text_path = root / text_rel
        preview_paragraphs: list[str] = []
        remaining_preview_characters = PREVIEW_TOTAL_CHARACTER_LIMIT
        all_paragraphs = [paragraph.text for paragraph in paragraphs]
        for paragraph in all_paragraphs[:PREVIEW_PARAGRAPH_LIMIT]:
            if remaining_preview_characters <= 0:
                break
            bounded = paragraph[: min(PREVIEW_PARAGRAPH_CHARACTER_LIMIT, remaining_preview_characters)]
            preview_paragraphs.append(bounded)
            remaining_preview_characters -= len(bounded)
        preview = {
            "source_structure_version": SOURCE_STRUCTURE_VERSION,
            "source_parser_version": SOURCE_PARSER_VERSION,
            "paragraph_layout": resolved_layout,
            "paragraph_layout_recommended": "NEWLINE" if recommends_newline_paragraphs(text) else None,
            "paragraph_layout_options": ["BLANK_LINE", "NEWLINE"],
            "character_count": len(text),
            "paragraph_count": len(all_paragraphs),
            "paragraphs": preview_paragraphs,
            "chapters": chapters,
            "chapter_count": len(chapters),
            "preview_character_limit": PREVIEW_TOTAL_CHARACTER_LIMIT,
            "preview_truncated": len(all_paragraphs) > len(preview_paragraphs)
            or any(len(original) > len(shown) for original, shown in zip(all_paragraphs, preview_paragraphs, strict=False)),
            "offset_unit": "UNICODE_CODEPOINT",
            "requires_llm_confirmation": True,
        }
        mime = media.get("mime_type", "application/octet-stream")
        metadata = {
            "media_version_id": media["media_version_id"],
            "paragraph_count": len(all_paragraphs),
            "character_count": len(text),
            "offset_unit": "UNICODE_CODEPOINT",
            "parser_version": SOURCE_PARSER_VERSION,
            "structure_version": SOURCE_STRUCTURE_VERSION,
            "paragraph_layout": resolved_layout,
            "paragraph_layout_request": requested_layout,
        }
        version_no = self._next_version_no(source_document_id)
        self._write_extracted_text(text_path, text)
        try:
            with self.database.transaction() as connection:
                if not rows:
                    connection.execute(
                        "INSERT INTO source_documents (id, project_id, code, title, source_kind, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, 'SCRIPT', ?, ?, ?, 1, 'v2')",
                        (source_document_id, project_id, source_code, source.stem[:200], now, now, actor),
                    )
                connection.execute(
                    """INSERT INTO source_document_versions
                    (id, source_document_id, version_no, rel_path, source_name, mime_type, byte_size, sha256, text_sha256,
                     extracted_text_rel, parse_status, parser_version, structure_version, metadata_json, created_at, updated_at,
                     created_by, revision, schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PARSED', ?, ?, ?, ?, ?, ?, 1, 'v2')""",
                    (
                        source_version_id, source_document_id, version_no, media["rel_path"], source.name, mime,
                        media["byte_size"], digest, text_hash, text_rel.as_posix(), SOURCE_PARSER_VERSION,
                        SOURCE_STRUCTURE_VERSION, _json(metadata), now, now, actor,
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
                    (
                        str(uuid.uuid4()), session_id, len(text),
                        _json({
                            "paragraph_count": len(all_paragraphs),
                            "source_sha256": digest,
                            "source_structure_version": SOURCE_STRUCTURE_VERSION,
                            "paragraph_layout": resolved_layout,
                        }),
                        now, now, actor,
                    ),
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
        except Exception:
            # NP04: a failed storage/output-contract step must never leave a
            # usable "ready" session or a half-written extracted authority.
            text_path.unlink(missing_ok=True)
            raise
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

    def _reuse_import(
        self,
        *,
        project_id: str,
        media: dict[str, Any],
        stored_source: dict[str, str],
        existing_version: Any,
        text: str,
        actor: str,
        requested: tuple[int, int] | None,
        requested_layout: ParagraphLayoutHint = "AUTO",
    ) -> dict[str, Any]:
        """Answer a repeat import without creating a second parse authority."""
        existing = dict(existing_version)
        with self.database.connect() as connection:
            session_row = connection.execute(
                """SELECT * FROM import_sessions
                WHERE source_document_version_id=? AND status IN ('PREVIEW_READY','COMMITTED')
                ORDER BY updated_at DESC, created_at DESC, id DESC LIMIT 1""",
                (existing["id"],),
            ).fetchone()
        if session_row is None or _parse_version(_load_json(session_row["preview_json"]).get("source_structure_version")) != SOURCE_STRUCTURE_VERSION:
            # The parse version is reusable but its preview belongs to an older
            # structure index (or is gone): rebuild a fresh preview session.
            session_row = self._recreate_preview_session(existing, text, actor, requested_layout)
        session = self.get_session(str(session_row["id"]))
        committed_scope = self._committed_scope(str(session["id"]), session.get("items") or [])
        if str(session["status"]) == "COMMITTED" and committed_scope is not None and requested is not None:
            # NP08: the same request range replays the original receipt; a
            # different range is a scope conflict, never a silent no-op.
            if requested != (committed_scope["source_paragraph_start"], committed_scope["source_paragraph_end"]):
                self._raise_scope_conflict(session, committed_scope, requested)
        index_status = self._replace_search_index(
            project_id, str(existing["source_document_id"]), Path(str(existing["source_name"])).stem, text, actor,
        )
        return {
            "source_document_id": str(existing["source_document_id"]),
            "source_document_version_id": str(existing["id"]),
            "import_session_id": str(session["id"]),
            "media_version_id": str(media["media_version_id"]),
            "stored_source": stored_source,
            "status": session["status"],
            "preview": session["preview"],
            "preview_hash": session["preview_hash"],
            "reused": True,
            "index_status": index_status,
        }

    def _recreate_preview_session(
        self,
        version: dict[str, Any],
        text: str,
        actor: str,
        requested_layout: ParagraphLayoutHint = "AUTO",
    ) -> Any:
        """Create a fresh PREVIEW_READY session for an existing parse version."""
        metadata = _load_json(version.get("metadata_json"))
        stored_layout = self._version_paragraph_layout(version)
        layout = stored_layout if requested_layout == "AUTO" else requested_layout
        paragraphs = source_paragraphs(text, resolve_paragraph_layout(text, layout))
        if not paragraphs:
            raise DomainRuleError("IMPORT_SOURCE_EMPTY", "解析后的正文没有可用段落")
        chapters = source_chapters(paragraphs, layout)
        session_id = str(uuid.uuid4())
        now = _utc_now()
        preview = {
            "source_structure_version": SOURCE_STRUCTURE_VERSION,
            "source_parser_version": SOURCE_PARSER_VERSION,
            "paragraph_layout": layout,
            "paragraph_layout_recommended": "NEWLINE" if recommends_newline_paragraphs(text) else None,
            "paragraph_layout_options": ["BLANK_LINE", "NEWLINE"],
            "character_count": len(text),
            "paragraph_count": len(paragraphs),
            "paragraphs": [
                paragraph.text[:PREVIEW_PARAGRAPH_CHARACTER_LIMIT] for paragraph in paragraphs[:PREVIEW_PARAGRAPH_LIMIT]
            ],
            "chapters": chapters,
            "chapter_count": len(chapters),
            "preview_character_limit": PREVIEW_TOTAL_CHARACTER_LIMIT,
            "preview_truncated": len(paragraphs) > PREVIEW_PARAGRAPH_LIMIT,
            "offset_unit": "UNICODE_CODEPOINT",
            "requires_llm_confirmation": True,
        }
        media_version_id = str(metadata.get("media_version_id") or "")
        if not media_version_id:
            raise DomainRuleError("IMPORT_MEDIA_VERSION_MISSING", "源文档版本缺少受控原文件版本，无法重建预览")
        with self.database.transaction() as connection:
            project_id = str(connection.execute(
                "SELECT project_id FROM source_documents WHERE id=?",
                (str(version["source_document_id"]),),
            ).fetchone()["project_id"])
            connection.execute(
                """INSERT INTO import_sessions
                (id, project_id, source_document_version_id, session_kind, status, preview_json, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, 'SCRIPT', 'PREVIEW_READY', ?, ?, ?, ?, 1, 'v2')""",
                (session_id, project_id, str(version["id"]), _json(preview), now, now, actor),
            )
            connection.execute(
                """INSERT INTO import_session_items
                (id, session_id, item_type, source_start, source_end, payload_json, validation_status, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, 'DOCUMENT_PREVIEW', 0, ?, ?, 'VALID', ?, ?, ?, 1, 'v2')""",
                (
                    str(uuid.uuid4()), session_id, len(text),
                    _json({
                        "paragraph_count": len(paragraphs),
                        "source_sha256": str(version["sha256"]),
                        "source_structure_version": SOURCE_STRUCTURE_VERSION,
                        "paragraph_layout": layout,
                    }),
                    now, now, actor,
                ),
            )
        with self.database.connect() as connection:
            return connection.execute("SELECT * FROM import_sessions WHERE id=?", (session_id,)).fetchone()

    def _next_version_no(self, source_document_id: str) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version_no), 0) FROM source_document_versions WHERE source_document_id=?",
                (source_document_id,),
            ).fetchone()
        return int(row[0]) + 1

    def latest_for_project(self, project_id: str) -> dict[str, Any] | None:
        """Restore the latest durable script-import workflow for a project."""
        self._invalidate_broken_preview_sessions(project_id)
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
        metadata = _load_json(version.get("metadata_json"))
        media_version_id = metadata.get("media_version_id") if isinstance(metadata, dict) else None
        if not isinstance(media_version_id, str) or not media_version_id:
            raise DomainRuleError("IMPORT_MEDIA_VERSION_MISSING", "最近导入会话缺少受控原文件版本")
        media = self.media.get_version(media_version_id)
        source_name = str(version.get("source_name") or "已导入原稿")
        selected_range = self._committed_scope(str(session["id"]), session.get("items") or [])
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

    def _invalidate_broken_preview_sessions(self, project_id: str) -> int:
        """Retire ready sessions whose stored preview has no usable paragraphs.

        Older parsers could persist ``PREVIEW_READY`` with
        ``paragraph_count == 0``; such a row can never satisfy the response
        contract, so it must not be restorable or reusable.
        """
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, preview_json FROM import_sessions WHERE project_id=? AND status='PREVIEW_READY'",
                (project_id,),
            ).fetchall()
        broken = [
            str(row["id"])
            for row in rows
            if int(_load_json(row["preview_json"]).get("paragraph_count") or 0) <= 0
        ]
        if not broken:
            return 0
        now = _utc_now()
        with self.database.transaction() as connection:
            for session_id in broken:
                connection.execute(
                    """UPDATE import_sessions
                    SET status='INVALID', error_summary=?, updated_at=?, revision=revision+1
                    WHERE id=? AND status='PREVIEW_READY'""",
                    ("解析结果没有可用段落，会话已作废；请重新导入以创建新的解析版本", now, session_id),
                )
        return len(broken)

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

    def _source_version_row(self, source_document_version_id: str) -> Any:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT sdv.id,sdv.extracted_text_rel,sdv.text_sha256,sdv.sha256,sdv.metadata_json,sdv.parser_version,
                sdv.structure_version,sd.project_id,sd.id AS source_document_id
                FROM source_document_versions sdv JOIN source_documents sd ON sd.id=sdv.source_document_id
                WHERE sdv.id=?""",
                (source_document_version_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("SOURCE_DOCUMENT_VERSION_NOT_FOUND", "源文档版本不存在")
        return row

    def _read_verified_extracted_text(self, version: Any, project_root: Path | None = None) -> str:
        """Read the frozen extracted text after verifying its recorded hash.

        NP10: the passage endpoint and the paragraph endpoint share this single
        verification rule, so neither can hand out text that no longer matches
        the immutable source version identity.
        """
        root = project_root if project_root is not None else self.media._project_root(str(version["project_id"]))
        recorded = str(version["text_sha256"] or "")
        try:
            path = controlled_path(
                root,
                str(version["extracted_text_rel"]),
                must_exist=True,
                require_file=True,
                code="IMPORT_EXTRACTED_TEXT_MISSING",
            )
        except DomainRuleError as error:
            raise DomainRuleError(
                "IMPORT_EXTRACTED_TEXT_CHANGED",
                "解析后的不可变文本缺失，源文档版本不再可信",
                {"source_document_version_id": str(version["id"]), "cause": error.code},
            ) from error
        if not recorded or _sha256_file(path) != recorded:
            raise DomainRuleError(
                "IMPORT_EXTRACTED_TEXT_CHANGED",
                "解析后的不可变文本 hash 已变化，源文档版本不再可信",
                {"source_document_version_id": str(version["id"])},
            )
        with path.open("r", encoding="utf-8", newline="") as handle:
            return handle.read()

    def _write_extracted_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}.partial")
        partial.write_text(text, encoding="utf-8", newline="")
        partial.replace(path)

    def _replace_extracted_text(self, path: Path, text: str) -> None:
        """Keep a reused parse authority byte-identical (or repair a lost copy)."""
        if path.is_file() and _sha256_file(path) == _sha256_bytes(text.encode("utf-8")):
            return
        self._write_extracted_text(path, text)

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
        row = self._source_version_row(source_document_version_id)
        project_root = self.media._project_root(str(row["project_id"]))
        text = self._read_verified_extracted_text(row, project_root)
        metadata = _load_json(row["metadata_json"])
        total = metadata.get("character_count")
        passage = text[start:end]
        actual_end = start + len(passage)
        return {
            "source_document_version_id": source_document_version_id,
            "source_start": start,
            "source_end": actual_end,
            "requested_end": end,
            "offset_unit": "UNICODE_CODEPOINT",
            "text": passage,
            "text_sha256": _sha256_bytes(passage.encode("utf-8")),
            "source_text_sha256": str(row["text_sha256"]),
            "total_character_count": int(total) if isinstance(total, int) else (len(text) if text else None),
            "has_more": actual_end < len(text),
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
        result["preview"] = _load_json(result.pop("preview_json"))
        result["source_document_version"] = dict(version) if version else None
        result["items"] = [{**dict(item), "payload": _load_json(item["payload_json"])} for item in items]
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

    @staticmethod
    def _committed_scope(session_id: str, items: list[dict[str, Any]]) -> dict[str, Any] | None:
        for item in reversed(items):
            if item.get("item_type") != "SOURCE_BODY_RANGE":
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            try:
                start = int(payload["source_paragraph_start"])
                end = int(payload["source_paragraph_end"])
            except (KeyError, TypeError, ValueError):
                continue
            mode = str(payload.get("selection_mode") or "EXPLICIT")
            return {
                "source_paragraph_start": start,
                "source_paragraph_end": end,
                "source_paragraph_count": int(payload.get("source_paragraph_count") or (end - start + 1)),
                "selection_mode": mode if mode in _SCOPE_SELECTION_MODES else "EXPLICIT",
                "import_session_id": str(item.get("session_id") or session_id),
            }
        return None

    def _scope_identity(self, start: int, end: int, selection_mode: str) -> tuple[int, int, str]:
        return (int(start), int(end), selection_mode if selection_mode in _SCOPE_SELECTION_MODES else "EXPLICIT")

    def _raise_scope_conflict(
        self,
        session: dict[str, Any],
        existing_scope: dict[str, Any],
        requested: tuple[int, int],
    ) -> None:
        raise DomainRuleError(
            "IMPORT_COMMIT_SCOPE_CONFLICT",
            "该导入会话已确认其他正文范围；请使用新的分析选择命令创建独立会话，或重新读取当前范围",
            {
                "import_session_id": str(session["id"]),
                "existing_scope": existing_scope,
                "requested_scope": {
                    "source_paragraph_start": requested[0],
                    "source_paragraph_end": requested[1],
                    "source_paragraph_count": requested[1] - requested[0] + 1,
                },
            },
        )

    def get_paragraphs(self, session_id: str, *, start: int = 1, limit: int = 40) -> dict[str, Any]:
        """Page through the immutable server-parsed paragraph authority."""
        session = self.get_session(session_id)
        version = session.get("source_document_version")
        if not version or version.get("parse_status") != "PARSED":
            raise DomainRuleError("IMPORT_SOURCE_NOT_PARSED", "源文档版本尚未完成解析")
        project_root = self.media._project_root(str(session["project_id"]))
        text = self._read_verified_extracted_text(version, project_root)
        layout = self._version_paragraph_layout(version, session["preview"])
        paragraphs = source_paragraphs(text, layout)
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

    def _version_paragraph_layout(
        self,
        version: dict[str, Any],
        preview: dict[str, Any] | None = None,
    ) -> ParagraphLayoutHint:
        candidates = [
            _load_json(version.get("metadata_json")).get("paragraph_layout"),
            (preview or {}).get("paragraph_layout"),
        ]
        for candidate in candidates:
            if candidate == "AUTO" or candidate == "BLANK_LINE" or candidate == "NEWLINE":
                return candidate
        return "BLANK_LINE"

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
        if (source_paragraph_start is None) != (source_paragraph_end is None):
            raise DomainRuleError("IMPORT_BODY_RANGE_INCOMPLETE", "正文范围必须同时提供起始段和结束段")
        requested_scope = (
            self._scope_identity(source_paragraph_start, source_paragraph_end, "EXPLICIT")
            if source_paragraph_start is not None and source_paragraph_end is not None
            else None
        )
        version = session["source_document_version"]
        if not version or version["parse_status"] != "PARSED":
            raise DomainRuleError("IMPORT_SOURCE_NOT_PARSED", "源文档版本尚未完成解析")
        metadata = _load_json(version["metadata_json"])
        self.media.verify_content_integrity(str(metadata["media_version_id"]))
        project_root = self.media._project_root(str(session["project_id"]))
        text = self._read_verified_extracted_text(version, project_root)
        paragraphs = source_paragraphs(text, self._version_paragraph_layout(version, session["preview"]))
        if not paragraphs:
            raise DomainRuleError("IMPORT_SOURCE_EMPTY", "解析后的正文没有可用段落")
        if session["status"] == "COMMITTED":
            # NP08: the idempotency identity includes the final normalised range.
            committed_scope = self._committed_scope(session_id, session["items"])
            if committed_scope is None:
                raise DomainRuleError(
                    "IMPORT_COMMIT_SCOPE_MISSING",
                    "该导入会话已确认但缺少正文范围记录，请重新导入或创建新的分析选择",
                )
            if requested_scope is not None and requested_scope[:2] != (
                committed_scope["source_paragraph_start"],
                committed_scope["source_paragraph_end"],
            ) or (requested_scope is None and (
                committed_scope["source_paragraph_start"] != 1
                or committed_scope["source_paragraph_end"] != len(paragraphs)
            )):
                self._raise_scope_conflict(session, committed_scope, requested_scope[:2] if requested_scope else (1, len(paragraphs)))
            return {
                **session,
                "idempotent": True,
                "source_preserved": True,
                "commit_snapshot": {
                    "source_document_version_id": session["source_document_version_id"],
                    "source_sha256": version["sha256"],
                    "text_sha256": version["text_sha256"],
                    "preview_hash": session["preview_hash"],
                    "validated_item_ids": [item["id"] for item in session["items"]],
                    "body_range": {key: value for key, value in committed_scope.items() if key != "import_session_id"},
                },
            }
        if session["status"] != "PREVIEW_READY":
            raise DomainRuleError("IMPORT_SESSION_NOT_READY", "只有校验通过的 PREVIEW_READY 导入会话可提交")
        if not session["validation"]["valid"]:
            raise DomainRuleError("IMPORT_VALIDATION_FAILED", "导入预览仍有校验问题", {"issues": self.get_issues(session_id)})
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
            else:
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
        if idempotent_race:
            committed_scope = self._committed_scope(session_id, self.get_session(session_id)["items"])
            if committed_scope is not None and (
                committed_scope["source_paragraph_start"], committed_scope["source_paragraph_end"]
            ) != (range_start, range_end):
                self._raise_scope_conflict(session, committed_scope, (range_start, range_end))
        committed = self.get_session(session_id)
        return {**committed, "idempotent": idempotent_race, "source_preserved": True, "commit_snapshot": snapshot}

    def create_selection(
        self,
        session_id: str,
        *,
        source_paragraph_start: int,
        source_paragraph_end: int,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Authorise a new analysis range over the same immutable source version.

        NP08: this never rewrites the frozen text or the original session; it
        creates a second session that carries the new authorised range so a
        different scope is an explicit, auditable operation instead of a
        silently ignored duplicate request.
        """
        session = self.get_session(session_id)
        if session["status"] != "COMMITTED":
            raise DomainRuleError("IMPORT_SESSION_NOT_COMMITTED", "只有已确认的导入会话可以派生新的正文范围")
        version = session["source_document_version"]
        if not version or version["parse_status"] != "PARSED":
            raise DomainRuleError("IMPORT_SOURCE_NOT_PARSED", "源文档版本尚未完成解析")
        project_root = self.media._project_root(str(session["project_id"]))
        text = self._read_verified_extracted_text(version, project_root)
        paragraphs = source_paragraphs(text, self._version_paragraph_layout(version, session["preview"]))
        if not paragraphs:
            raise DomainRuleError("IMPORT_SOURCE_EMPTY", "解析后的正文没有可用段落")
        if source_paragraph_start < 1 or source_paragraph_end < source_paragraph_start or source_paragraph_end > len(paragraphs):
            raise DomainRuleError(
                "IMPORT_BODY_RANGE_INVALID",
                f"正文范围必须位于 1–{len(paragraphs)} 段内，且结束段不得早于起始段",
                {"total_paragraph_count": len(paragraphs)},
            )
        existing_scope = self._committed_scope(session_id, session["items"])
        if existing_scope is not None and (
            existing_scope["source_paragraph_start"], existing_scope["source_paragraph_end"]
        ) == (source_paragraph_start, source_paragraph_end):
            return {
                **session,
                "import_session_id": session_id,
                "selected_range": {key: value for key, value in existing_scope.items() if key != "import_session_id"},
                "reused": True,
                "source_preserved": True,
            }
        new_session_id = str(uuid.uuid4())
        now = _utc_now()
        preview = dict(session["preview"])
        preview["derived_from_import_session_id"] = session_id
        preview["authorised_scope"] = {
            "source_paragraph_start": source_paragraph_start,
            "source_paragraph_end": source_paragraph_end,
        }
        body_range = {
            "source_paragraph_start": source_paragraph_start,
            "source_paragraph_end": source_paragraph_end,
            "source_paragraph_count": source_paragraph_end - source_paragraph_start + 1,
            "selection_mode": "EXPLICIT",
        }
        snapshot = {
            "source_document_version_id": session["source_document_version_id"],
            "source_sha256": version["sha256"],
            "text_sha256": version["text_sha256"],
            "preview_hash": session["preview_hash"],
            "validated_item_ids": [item["id"] for item in session["items"] if item["item_type"] == "DOCUMENT_PREVIEW"],
            "body_range": body_range,
            "derived_from_import_session_id": session_id,
        }
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO import_sessions
                (id, project_id, source_document_version_id, session_kind, status, preview_json, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, 'SCRIPT', 'COMMITTED', ?, ?, ?, ?, 1, 'v2')""",
                (new_session_id, str(session["project_id"]), str(session["source_document_version_id"]), _json(preview), now, now, actor),
            )
            for item in session["items"]:
                if item["item_type"] != "DOCUMENT_PREVIEW":
                    continue
                connection.execute(
                    """INSERT INTO import_session_items
                    (id, session_id, item_type, source_start, source_end, payload_json, validation_status, created_at, updated_at, created_by, revision, schema_version)
                    VALUES (?, ?, 'DOCUMENT_PREVIEW', ?, ?, ?, ?, ?, ?, ?, 1, 'v2')""",
                    (
                        str(uuid.uuid4()), new_session_id, item["source_start"], item["source_end"],
                        _json(item["payload"]), item["validation_status"], now, now, actor,
                    ),
                )
            connection.execute(
                """INSERT INTO import_session_items
                (id, session_id, item_type, source_start, source_end, payload_json, validation_status, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, 'SOURCE_BODY_RANGE', ?, ?, ?, 'VALID', ?, ?, ?, 1, 'v2')""",
                (
                    str(uuid.uuid4()), new_session_id,
                    paragraphs[source_paragraph_start - 1].start,
                    paragraphs[source_paragraph_end - 1].end,
                    _json(body_range), now, now, actor,
                ),
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', 'IMPORT_SELECTION_CREATED', 'import_session', ?, ?, ?)",
                (actor, new_session_id, "为同一不可变原稿创建新的正文范围分析选择", _json(snapshot)),
            )
        created = self.get_session(new_session_id)
        return {
            **created,
            "import_session_id": new_session_id,
            "selected_range": body_range,
            "reused": False,
            "source_preserved": True,
            "commit_snapshot": snapshot,
        }

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

        if source_paragraph_start is None and source_paragraph_end is None:
            # The session's authorised range is the default scope for the
            # downstream pipeline; an explicit request range still wins.
            session = self.get_session(session_id)
            committed_scope = self._committed_scope(session_id, session["items"])
            if committed_scope is not None:
                source_paragraph_start = int(committed_scope["source_paragraph_start"])
                source_paragraph_end = int(committed_scope["source_paragraph_end"])
        return LocalLLMService(self.database, self.settings).enqueue_breakdown(
            session_id,
            profile_version_id,
            idempotency_key,
            target_episode_id=target_episode_id,
            source_paragraph_start=source_paragraph_start,
            source_paragraph_end=source_paragraph_end,
        )

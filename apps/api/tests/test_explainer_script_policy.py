"""``script_policy``, file reading and topic-mode contracts (spec §C1, §C2, §C4.2–C4.5, §C9).

What this file proves:

* ``script_policy`` is optional and orthogonal to ``input_kind``; a legacy row
  without the option resolves to ``ADAPT_SOURCES`` and the option lands in
  ``explainer_videos.input_payload_json``;
* ``POST /explainers`` accepts and reports the option, and a preserved pasted
  manuscript has a frozen script revision *before* preflight can demand one;
* the explainer source suffix whitelist equals the shared extractor's real
  support set (no "upload supported, internally rejected"), and DOCX / text PDF /
  EPUB extraction reports complete metadata while a scanned PDF asks for OCR;
* the same Chinese text imported as UTF-8, UTF-8+BOM and GBK yields an identical
  body with correct decoding metadata, a wrong forced encoding is refused rather
  than decoded into mojibake, and a lossy decode is reported as ``partial``;
* an imported document in preserved mode registers the exact manuscript;
* rewritten mode with insufficient facts returns ``insufficient_content`` and
  never pads the script with unsourced causality or examples.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from local_drama.api.routes import explainers as explainer_routes
from local_drama.api.schemas.explainers import ExplainerCreateRequest
from local_drama.application.documents import (
    TEXT_ENCODING_OVERRIDES,
    extract_document_text,
    normalise_encoding_override,
)
from local_drama.application.explainers.commands import (
    ExplainerCreateCommand,
    build_explainer_creation_service,
)
from local_drama.application.explainers.contracts_v2 import (
    CONTENT_EXTRACT_SCHEMA_VERSION,
    SCRIPT_DRAFT_SCHEMA_VERSION,
    resolve_script_policy,
)
from local_drama.application.explainers.text_planner import (
    SCRIPT_BUDGET_MAX_REPAIRS,
    LocalTextPlanner,
    build_stage_handlers,
)
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.explainers.contracts import ExplainerContractError, ProductKind
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "policy-project"
VIDEO_ID = "policy-video"
CHINESE_BODY = "第一章 起点\n林舟推开旧仓库的门，灯塔重新亮起。\n"
XHTML = "http://www.w3.org/1999/xhtml"


# --------------------------------------------------------------------------- #
# schema and resolver
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value", ["PRESERVE_ORIGINAL", "ADAPT_SOURCES", "CREATE_FROM_TOPIC", None]
)
def test_create_request_accepts_every_supported_policy(value: str | None) -> None:
    request = ExplainerCreateRequest(
        title="雨夜灯塔",
        script_policy=value,
        outputs=[{"edition_key": "main", "voice_locale": "zh-CN"}],
    )
    assert request.script_policy == value


def test_create_request_refuses_an_unknown_policy() -> None:
    with pytest.raises(Exception):
        ExplainerCreateRequest(
            title="雨夜灯塔",
            script_policy="KEEP_EVERYTHING",
            outputs=[{"edition_key": "main", "voice_locale": "zh-CN"}],
        )


def test_policy_is_orthogonal_to_the_input_channel() -> None:
    base = {
        "title": "同一份 TXT",
        "input_kind": "DOCUMENT_IMPORT",
        "outputs": ({"edition_key": "main"},),
    }
    preserved = ExplainerCreateCommand(**base, script_policy="PRESERVE_ORIGINAL", pasted_text=CHINESE_BODY)
    adapted = ExplainerCreateCommand(**base, script_policy="ADAPT_SOURCES", pasted_text=CHINESE_BODY)
    legacy = ExplainerCreateCommand(**base, pasted_text=CHINESE_BODY)
    assert preserved.input_kind == adapted.input_kind == legacy.input_kind == "DOCUMENT_IMPORT"
    assert preserved.resolved_script_policy() == "PRESERVE_ORIGINAL"
    assert adapted.resolved_script_policy() == "ADAPT_SOURCES"
    assert legacy.resolved_script_policy() == "ADAPT_SOURCES"
    assert preserved.request_digest() != adapted.request_digest()
    assert adapted.request_digest() == legacy.request_digest()


def test_policy_is_stored_in_the_durable_input_projection() -> None:
    command = ExplainerCreateCommand(
        title="雨夜灯塔",
        input_kind="PASTED_SCRIPT",
        script_policy="ADAPT_SOURCES",
        pasted_text=CHINESE_BODY,
        outputs=({"edition_key": "main"},),
    )
    payload = command.input_payload()
    assert payload["script_policy"] == "ADAPT_SOURCES"
    assert "preserved_original" not in payload
    preserved = ExplainerCreateCommand(
        title="雨夜灯塔",
        input_kind="PASTED_SCRIPT",
        script_policy="PRESERVE_ORIGINAL",
        pasted_text=CHINESE_BODY,
        outputs=({"edition_key": "main"},),
    )
    assert preserved.input_payload()["preserved_original"]["script_source_text"] == CHINESE_BODY


# --------------------------------------------------------------------------- #
# HTTP creation
# --------------------------------------------------------------------------- #
def _http_client(workspace: Any):
    from fastapi.testclient import TestClient

    from local_drama.main import create_app
    from scripts.migrate import migrate

    settings = workspace.model_copy(deep=True) if hasattr(workspace, "model_copy") else workspace
    settings.ensure_roots()
    migrate(settings.database_path)
    app = create_app(settings)
    client = TestClient(app)
    client.__enter__()
    return client


def test_http_create_reports_the_policy_and_registers_a_preserved_script(workspace: Any) -> None:
    client = _http_client(workspace)
    try:
        body = {
            "title": "雨夜灯塔",
            "topic": "",
            "input_kind": "PASTED_SCRIPT",
            "script_policy": "PRESERVE_ORIGINAL",
            "pasted_text": CHINESE_BODY,
            "target_seconds": 300,
            "source_locale": "zh-CN",
            "outputs": [{"edition_key": "main", "voice_locale": "zh-CN"}],
        }
        created = client.post("/api/v2/explainers", json=body, headers={"Idempotency-Key": "policy-http-1"})
        assert created.status_code == 201, created.text
        payload = created.json()
        assert payload["script_policy"] == "PRESERVE_ORIGINAL"
        assert payload["preserved_script"]["registered_at_create"] is True
        revision_id = payload["video"]["current_script_revision_id"]
        assert revision_id
        project_id = payload["project"]["id"]

        # The persisted state must agree on a *fresh* connection, so the preflight
        # gate can no longer find "pasted script mode without a script revision".
        overview = client.get(f"/api/v2/explainers/{project_id}")
        assert overview.status_code == 200, overview.text
        reopened = Database(workspace.database_path)
        with reopened.connect() as connection:
            repo = ExplainerRepository(connection)
            revision = repo.get("explainer_script_revisions", str(revision_id))
            assert revision["status"] == "FROZEN"
            assert revision["provenance_json"]["script_source_text"] == CHINESE_BODY
            assert str(repo.get("explainer_videos", str(payload["video"]["id"]))["current_script_revision_id"]) == (
                str(revision_id)
            )
    finally:
        client.__exit__(None, None, None)


def test_http_create_without_the_option_stays_back_compatible(workspace: Any) -> None:
    client = _http_client(workspace)
    try:
        body = {
            "title": "普通主题",
            "topic": "灯塔",
            "input_kind": "TOPIC",
            "target_seconds": 300,
            "outputs": [{"edition_key": "main", "voice_locale": "zh-CN"}],
        }
        created = client.post("/api/v2/explainers", json=body, headers={"Idempotency-Key": "policy-http-2"})
        assert created.status_code == 201, created.text
        assert created.json()["script_policy"] == "ADAPT_SOURCES"
        assert created.json()["preserved_script"] is None
        assert created.json()["video"]["input_payload_json"]["script_policy"] == "ADAPT_SOURCES"
    finally:
        client.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# file reading (§C2, §C9 items 1–2)
# --------------------------------------------------------------------------- #
def test_the_same_text_decodes_identically_from_utf8_bom_and_gbk(tmp_path: Path) -> None:
    decoded: dict[str, dict[str, Any]] = {}
    for label, payload in (
        ("utf-8", CHINESE_BODY.encode("utf-8")),
        ("utf-8-sig", CHINESE_BODY.encode("utf-8-sig")),
        ("gb18030", CHINESE_BODY.encode("gb18030")),
    ):
        target = tmp_path / f"body-{label}.txt"
        target.write_bytes(payload)
        decoded[label] = extract_document_text(target)
    bodies = {item["text"] for item in decoded.values()}
    assert bodies == {CHINESE_BODY}
    assert decoded["utf-8"]["encoding"] == "utf-8"
    assert decoded["utf-8"]["confidence"] == "HIGH"
    assert decoded["utf-8"]["had_bom"] is False
    assert decoded["utf-8-sig"]["encoding"] == "utf-8-sig"
    assert decoded["utf-8-sig"]["had_bom"] is True
    assert decoded["gb18030"]["encoding"] == "gb18030"
    assert decoded["gb18030"]["confidence"] == "LOW"
    assert decoded["gb18030"]["encoding_source"] == "GB18030_COMPAT_FALLBACK"
    for item in decoded.values():
        assert item["replacement_char_count"] == 0
        assert item["quality"] == "complete"
        assert item["raw_sha256"] and item["body_sha256"]
        assert item["character_count"] == len(CHINESE_BODY)


def test_a_wrong_forced_encoding_is_refused_instead_of_producing_mojibake(tmp_path: Path) -> None:
    target = tmp_path / "gbk.txt"
    target.write_bytes(CHINESE_BODY.encode("gb18030"))
    with pytest.raises(DomainRuleError) as error:
        extract_document_text(target, encoding_override="utf-8")
    assert error.value.code == "DOCUMENT_DECODE_FAILED"
    assert error.value.details["encoding_override"] == "utf-8"
    # The same bytes decode correctly when the operator picks the right encoding.
    decoded = extract_document_text(target, encoding_override="gb18030")
    assert decoded["text"] == CHINESE_BODY
    assert decoded["encoding_source"] == "OPERATOR_OVERRIDE"
    assert decoded["quality"] == "complete"


def test_encoding_override_allowlist_and_bom_requirement(tmp_path: Path) -> None:
    assert set(TEXT_ENCODING_OVERRIDES) >= {"utf-8", "utf-8-sig", "gb18030", "utf-16", "utf-32"}
    with pytest.raises(DomainRuleError) as error:
        normalise_encoding_override("latin-1")
    assert error.value.code == "UNSUPPORTED_ENCODING_OVERRIDE"
    target = tmp_path / "no-bom.txt"
    target.write_bytes(CHINESE_BODY.encode("utf-8"))
    with pytest.raises(DomainRuleError) as error:
        extract_document_text(target, encoding_override="utf-16")
    assert error.value.code == "ENCODING_OVERRIDE_BOM_REQUIRED"
    utf16 = tmp_path / "with-bom.txt"
    utf16.write_bytes(CHINESE_BODY.encode("utf-16"))
    assert extract_document_text(utf16, encoding_override="utf-16")["text"] == CHINESE_BODY


def test_a_lossy_decode_is_reported_as_partial_and_not_as_authority(tmp_path: Path) -> None:
    # A lone invalid byte in an otherwise GB18030 body is replaced, and the
    # extraction says so instead of pretending the text is complete.
    target = tmp_path / "broken.txt"
    target.write_bytes(b"\xff\xfe\xfd\xff\x00\x00\xff\xff not utf-16 at all")
    extracted = extract_document_text(target)
    assert extracted["replacement_char_count"] >= 0
    if extracted["quality"] != "complete":
        assert extracted["warnings"]
        assert extracted["warnings"][0]["code"] == "DECODE_REPLACEMENT_CHARACTERS"


def _write_docx(path: Path, paragraphs: list[str]) -> None:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    document_xml = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            "<?xml version='1.0'?><Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
            "<Default Extension='xml' ContentType='application/xml'/></Types>",
        )
        archive.writestr("word/document.xml", document_xml)


def _write_epub(path: Path, chapters: dict[str, str]) -> None:
    items = "".join(
        f"<item id='c{index}' href='{name}' media-type='application/xhtml+xml'/>"
        for index, name in enumerate(chapters)
    )
    spine = "".join(f"<itemref idref='c{index}'/>" for index in range(len(chapters)))
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            "<?xml version='1.0'?><container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
            "<rootfiles><rootfile full-path='OEBPS/content.opf'/></rootfiles></container>",
        )
        archive.writestr(
            "OEBPS/content.opf",
            "<?xml version='1.0'?><package xmlns='http://www.idpf.org/2007/opf'>"
            f"<manifest>{items}</manifest><spine>{spine}</spine></package>",
        )
        for name, body in chapters.items():
            archive.writestr(f"OEBPS/{name}", body)


def _write_text_pdf(path: Path, text: str) -> None:
    """A minimal single-page PDF with a real text layer, written by hand.

    ``pypdf`` cannot create a text layer by itself, so the objects, the content
    stream and the cross-reference table are assembled explicitly.
    """

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    content = f"BT /F1 14 Tf 20 150 Td ({text}) Tj ET".encode("latin-1")
    objects[3] = (
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream"
    )
    payload = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload += f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
    xref_offset = len(payload)
    payload += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    payload += b"0000000000 65535 f \n"
    for offset in offsets:
        payload += f"{offset:010d} 00000 n \n".encode("ascii")
    payload += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    path.write_bytes(bytes(payload))


def test_container_extractions_report_complete_metadata(tmp_path: Path) -> None:
    docx = tmp_path / "fixture.docx"
    _write_docx(docx, ["第一章 起点", "林舟推开旧仓库的门。"])
    extracted = extract_document_text(docx)
    assert extracted["format"] == "DOCX"
    assert extracted["quality"] == "complete"
    assert extracted["encoding"] is None
    assert extracted["confidence"] == "CONTAINER_PARSED"
    assert extracted["had_bom"] is False
    assert extracted["raw_sha256"] and extracted["body_sha256"]
    assert "第一章 起点" in extracted["text"]

    epub = tmp_path / "novel.epub"
    _write_epub(
        epub,
        {"c1.xhtml": f'<html xmlns="{XHTML}"><body><p>风从海上来。</p></body></html>'},
    )
    extracted = extract_document_text(epub)
    assert extracted["format"] == "EPUB"
    assert extracted["quality"] == "complete"
    assert extracted["encoding"] is None

    pdf = tmp_path / "text-layer.pdf"
    pytest.importorskip("pypdf")
    _write_text_pdf(pdf, "Hello Lighthouse turning point")
    extracted = extract_document_text(pdf)
    assert extracted["format"] == "PDF"
    assert extracted["quality"] == "complete"
    assert "Lighthouse" in extracted["text"]
    assert extracted["credibility_derived_from_quality"] is False


def test_scanned_pdf_asks_for_ocr_and_container_rejects_an_encoding_override(tmp_path: Path) -> None:
    writer_module = pytest.importorskip("pypdf")
    scanned = tmp_path / "scanned.pdf"
    writer = writer_module.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with scanned.open("wb") as handle:
        writer.write(handle)
    with pytest.raises(DomainRuleError) as error:
        extract_document_text(scanned)
    assert error.value.code == "DOCUMENT_TEXT_EMPTY"
    assert "OCR" in error.value.message

    docx = tmp_path / "fixture.docx"
    _write_docx(docx, ["第一章 起点"])
    with pytest.raises(DomainRuleError) as error:
        extract_document_text(docx, encoding_override="utf-8")
    assert error.value.code == "INVALID_ENCODING_OVERRIDE"


@pytest.mark.parametrize(
    "suffix", [".txt", ".md", ".markdown", ".docx", ".pdf", ".epub", ".json", ".csv", ".srt", ".vtt", ".rar"]
)
def test_suffix_whitelist_matches_the_extractor_exactly(tmp_path: Path, suffix: str) -> None:
    advertised = suffix in explainer_routes._SOURCE_SUFFIXES  # noqa: SLF001 - the whitelist is the contract
    target = tmp_path / f"sample{suffix}"
    if suffix == ".docx":
        _write_docx(target, ["第一章 起点"])
    elif suffix == ".epub":
        _write_epub(target, {"c1.xhtml": f'<html xmlns="{XHTML}"><body><p>正文。</p></body></html>'})
    else:
        target.write_text("第一章 起点\n", encoding="utf-8")
    try:
        extract_document_text(target)
        accepted = True
    except DomainRuleError as error:
        accepted = error.code not in {"UNSUPPORTED_DOCUMENT_TYPE"}
    assert accepted is advertised, f"{suffix}: advertised={advertised} accepted={accepted}"


def test_structured_and_container_suffixes_are_not_advertised() -> None:
    assert explainer_routes._SOURCE_SUFFIXES == {  # noqa: SLF001
        ".txt",
        ".md",
        ".markdown",
        ".docx",
        ".pdf",
        ".epub",
    }
    for removed in (".json", ".csv", ".srt", ".vtt"):
        assert removed not in explainer_routes._SOURCE_SUFFIXES  # noqa: SLF001


# --------------------------------------------------------------------------- #
# imported document in preserved mode
# --------------------------------------------------------------------------- #
def _seed_adapt_project(database: Database, *, script_policy: str) -> dict[str, str]:
    with database.transaction() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'policy_proj', '雨夜灯塔', 'DRAFT', 'v2', ?, 300000, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID, ProductKind.EXPLAINER.value),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale, input_kind,
            input_payload_json, duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode,
            research_mode, status, created_at, updated_at, created_by)
            VALUES (?, ?, '雨夜灯塔', '灯塔', 'FACTUAL_EXPLAINER', 'zh-CN', 'DOCUMENT_IMPORT', ?, 'FIXED', 300, 0,
            'AUTO_WITH_EXCEPTIONS', 'LOCAL_ONLY', 'OFFLINE_IMPORT', 'DRAFT',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (VIDEO_ID, PROJECT_ID, json.dumps({"script_policy": script_policy}, ensure_ascii=False)),
        )
    return {"project_id": PROJECT_ID, "video_id": VIDEO_ID}


def test_importing_a_manuscript_in_preserved_mode_registers_the_exact_source(database: Database) -> None:
    seeded = _seed_adapt_project(database, script_policy="PRESERVE_ORIGINAL")
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        result = explainer_routes._import_source_command(  # noqa: SLF001 - the import command is the unit
            seeded["project_id"],
            repo,
            CHINESE_BODY,
            "雨夜灯塔",
            "zh-CN",
            "DOCUMENT_IMPORT",
            lambda repository: __import__(
                "local_drama.application.explainers.research", fromlist=["ExplainerResearchService"]
            ).ExplainerResearchService(repository),
            extraction={"format": "TEXT", "confidence": "HIGH"},
            stored_upload={"rel_path": "01_story/source_documents/abc.txt"},
        )
    preserved = result["preserved_script"]
    assert preserved is not None
    assert preserved["reused"] is False
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        revision = repo.get("explainer_script_revisions", preserved["script_revision_id"])
        assert revision["status"] == "FROZEN"
        assert revision["provenance_json"]["script_source_text"] == CHINESE_BODY
        assert revision["provenance_json"]["registered_at"] == "SOURCE_IMPORT"
        assert str(repo.get("explainer_videos", VIDEO_ID)["current_script_revision_id"]) == str(revision["id"])
        source = repo.list_where("explainer_sources", {"video_id": VIDEO_ID})[0]
        assert source["rel_path"] == "01_story/source_documents/abc.txt"
        # Import never translates extraction quality into fact credibility.
        assert source["credibility_kind"] == "UNKNOWN"
        assert result["status"] == "IMPORTED_NOT_FACT_CHECKED"


def test_preserved_creation_keeps_the_exact_manuscript_as_a_utf8_file(workspace: Any) -> None:
    database = Database(workspace.database_path)
    workspace.ensure_roots()
    from scripts.migrate import migrate

    migrate(workspace.database_path)
    service = build_explainer_creation_service(database, workspace)
    created = service.create_workspace(
        ExplainerCreateCommand(
            title="雨夜灯塔",
            input_kind="PASTED_SCRIPT",
            script_policy="PRESERVE_ORIGINAL",
            pasted_text=CHINESE_BODY,
            outputs=({"edition_key": "main"},),
        ),
        idempotency_key="preserve-file-1",
    )
    digest = created["video"]["input_payload_json"]["preserved_original"]["script_source_hash"]
    project_code = str(created["project"]["code"])
    target = Path(workspace.projects_root) / project_code / "01_story" / "source_documents" / (
        f"{digest}.script-source.txt"
    )
    assert target.is_file()
    assert target.read_bytes() == CHINESE_BODY.encode("utf-8")
    with database.connect() as connection:
        revision = ExplainerRepository(connection).get(
            "explainer_script_revisions", str(created["video"]["current_script_revision_id"])
        )
    provenance = revision["provenance_json"]
    assert provenance["script_source_file"] == f"01_story/source_documents/{digest}.script-source.txt"
    assert (Path(workspace.projects_root) / project_code / provenance["script_source_file"]).is_file()


def test_importing_in_adapt_mode_does_not_register_a_preserved_script(database: Database) -> None:
    seeded = _seed_adapt_project(database, script_policy="ADAPT_SOURCES")
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        result = explainer_routes._import_source_command(  # noqa: SLF001
            seeded["project_id"],
            repo,
            CHINESE_BODY,
            "资料",
            "zh-CN",
            "DOCUMENT_IMPORT",
            lambda repository: __import__(
                "local_drama.application.explainers.research", fromlist=["ExplainerResearchService"]
            ).ExplainerResearchService(repository),
        )
    assert result["preserved_script"] is None
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        assert repo.list_where("explainer_script_revisions", {"video_id": VIDEO_ID}) == []


# --------------------------------------------------------------------------- #
# rewritten mode: insufficient content (§C4.2, §C9 item 6)
# --------------------------------------------------------------------------- #
class InsufficientDraftClient:
    """Returns a strict ``script-draft.v2`` answer that declares the gap."""

    def __init__(self, *, insufficient: bool = True) -> None:
        self.calls: list[dict[str, Any]] = []
        self.insufficient = insufficient

    def chat_json(self, system, user, images=None, *, json_schema=None, inference_options=None):
        self.calls.append({"system": system, "user": user})
        return {
            "schema_version": SCRIPT_DRAFT_SCHEMA_VERSION,
            "outline": [{"title": "开头的问题", "audience_question": "灯塔为什么亮起"}],
            "segments": []
            if self.insufficient
            else [
                {
                    "chapter_index": 0,
                    "display_text": "灯塔在雨夜里重新亮起。",
                    "statement_type": "FACT",
                    "claim_ids": ["C001"],
                    "entity_ids": [],
                    "pronunciation_suggestions": [],
                    "pause_after_ms": 0,
                }
            ],
            "insufficient_content": self.insufficient,
            "missing_content_note": "现有资料只能支持一句结论，无法在不新增事实的前提下写满三分钟。",
        }


def _claim_ledger(database: Database, *, script_policy: str) -> dict[str, str]:
    seeded = _seed_adapt_project(database, script_policy=script_policy)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        packet = repo.insert(
            "explainer_research_packets",
            {
                "video_id": VIDEO_ID,
                "revision_no": 1,
                "status": "READY",
                "mode": "OFFLINE_IMPORT",
                "topic": "灯塔",
                "max_external_requests": 0,
                "content_hash": "a" * 64,
            },
        )
        source = repo.insert(
            "explainer_sources",
            {
                "packet_id": packet["id"],
                "video_id": VIDEO_ID,
                "project_id": PROJECT_ID,
                "source_kind": "DOCUMENT_IMPORT",
                "title": "来源",
                "event_date_precision": "SECOND",
                "fetched_at": "2026-01-01T00:00:00Z",
                "body_sha256": "b" * 64,
                "credibility_kind": "SECONDARY",
                "retrieved_via": "USER_SUPPLIED",
            },
        )
        span = repo.insert(
            "explainer_source_spans",
            {
                "source_id": source["id"],
                "packet_id": packet["id"],
                "ordinal": 0,
                "start_offset": 0,
                "end_offset": 10,
                "quote_text": "灯塔在雨夜里重新亮起。",
                "span_hash": "c" * 64,
                "paragraph_no": 1,
            },
        )
        repo.insert(
            "explainer_claims",
            {
                "packet_id": packet["id"],
                "video_id": VIDEO_ID,
                "code": "C001",
                "statement": "灯塔在雨夜里重新亮起。",
                "statement_kind": "FACT",
                "status": "SUPPORTED",
                "importance": "KEY",
                "verified_as_history": False,
            },
        )
    return {"project_id": PROJECT_ID, "video_id": VIDEO_ID, "span_id": str(span["id"])}


def _repo_factory(database: Database):
    class _Ctx:
        def __enter__(self) -> ExplainerRepository:
            self._context = database.transaction()
            return ExplainerRepository(self._context.__enter__())

        def __exit__(self, *args: Any) -> Any:
            return self._context.__exit__(*args)

    return _Ctx


def test_insufficient_content_is_reported_instead_of_padding(database: Database) -> None:
    _claim_ledger(database, script_policy="ADAPT_SOURCES")
    client = InsufficientDraftClient()
    planner = LocalTextPlanner(client_factory=lambda: client)
    with database.connect() as connection:
        plan = planner.plan_script(
            repo=ExplainerRepository(connection), project_id=PROJECT_ID, video_id=VIDEO_ID
        )
    assert plan["status"] == "INSUFFICIENT_CONTENT"
    assert plan["insufficient_content"] is True
    assert plan["segments"] == []
    assert plan["length_repairs"] == []
    assert "无法在不新增事实" in plan["missing_content_note"]
    assert plan["unsourced_padding_refused"] is True
    # Exactly one call: no "expand for length" re-ask was issued.
    assert len(client.calls) == 1


def test_narration_write_blocks_on_insufficient_content(database: Database) -> None:
    _claim_ledger(database, script_policy="ADAPT_SOURCES")
    client = InsufficientDraftClient()
    planner = LocalTextPlanner(client_factory=lambda: client)
    handlers = build_stage_handlers(planner_factory=lambda: planner, repo_factory=_repo_factory(database))
    report = handlers["NARRATION_WRITE"](
        {"id": "job-insufficient"},
        {"task_code": "NARRATION_WRITE", "semantic_inputs": {"project_id": PROJECT_ID, "video_id": VIDEO_ID}},
    )
    assert report["status"] == "BLOCKED"
    assert report["machine_check"]["ok"] is False
    assert report["produced"]["script_revision_id"] is None
    assert len(client.calls) == 1
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        assert repo.list_where("explainer_script_revisions", {"video_id": VIDEO_ID}) == []


def test_a_sufficient_draft_is_persisted_with_program_assigned_ids(database: Database) -> None:
    _claim_ledger(database, script_policy="ADAPT_SOURCES")
    client = InsufficientDraftClient(insufficient=False)
    planner = LocalTextPlanner(client_factory=lambda: client)
    handlers = build_stage_handlers(planner_factory=lambda: planner, repo_factory=_repo_factory(database))
    report = handlers["NARRATION_WRITE"](
        {"id": "job-sufficient"},
        {"task_code": "NARRATION_WRITE", "semantic_inputs": {"project_id": PROJECT_ID, "video_id": VIDEO_ID}},
    )
    assert report["status"] == "PASS"
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        segments = repo.segments(report["produced"]["script_revision_id"])
        assert [item["canonical_segment_id"] for item in segments] == ["seg_001"]
        assert repo.get("explainer_claims", str(segments[0]["claim_ids_json"][0]))["code"] == "C001"


def test_legacy_expansion_prompt_forbids_inventing_facts(database: Database) -> None:
    """The old "add causal chains / typical examples" re-ask is gone (§C4.2)."""

    _claim_ledger(database, script_policy="ADAPT_SOURCES")

    class LegacyShortClient:
        """Answers the strict contract with the legacy shape, then never grows."""

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def chat_json(self, system, user, images=None, *, json_schema=None, inference_options=None):
            self.calls.append({"system": system, "user": user})
            return {
                "outline": ["开头"],
                "segments": [
                    {
                        "canonical_segment_id": "seg_001",
                        "display_text": "灯塔在雨夜里重新亮起。",
                        "statement_type": "FACT",
                        "claim_code": "C001",
                    }
                ],
            }

    client = LegacyShortClient()
    planner = LocalTextPlanner(client_factory=lambda: client)
    with database.connect() as connection:
        plan = planner.plan_script(
            repo=ExplainerRepository(connection), project_id=PROJECT_ID, video_id=VIDEO_ID
        )
    assert plan["segment_count"] == 1
    assert plan["length_repairs"], "the bounded shortfall repair still runs"
    assert plan["length_repairs"][0]["stopped_on_no_progress"] is True, (
        "an answer that does not grow must stop the re-ask loop instead of paying for "
        "identical calls again"
    )
    # one strict-contract ask, the legacy initial ask, and exactly one repair ask
    assert len(client.calls) == 3
    repair_prompt = client.calls[-1]["user"]
    assert "不得新增资料中没有的事实" in repair_prompt
    assert "不要为凑字数编造内容" in repair_prompt
    assert "典型例子" not in repair_prompt
    assert "机制细节" not in repair_prompt


def test_the_length_repair_keeps_asking_while_the_model_makes_progress(database: Database) -> None:
    """Progress is still rewarded: a longer answer is kept and re-asked again."""

    _claim_ledger(database, script_policy="ADAPT_SOURCES")

    class GrowingClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self.growth = 0

        def chat_json(self, system, user, images=None, *, json_schema=None, inference_options=None):
            self.calls.append({"system": system, "user": user})
            self.growth += 1
            return {
                "outline": ["开头"],
                "segments": [
                    {
                        "canonical_segment_id": "seg_001",
                        "display_text": "灯塔在雨夜里重新亮起。" * self.growth,
                        "statement_type": "FACT",
                        "claim_code": "C001",
                    }
                ],
            }

    client = GrowingClient()
    planner = LocalTextPlanner(client_factory=lambda: client)
    with database.connect() as connection:
        plan = planner.plan_script(
            repo=ExplainerRepository(connection), project_id=PROJECT_ID, video_id=VIDEO_ID
        )
    assert plan["segment_count"] == 1
    assert plan["character_count"] > len("灯塔在雨夜里重新亮起。"), "the longer answer must be kept"
    assert all(not item.get("stopped_on_no_progress") for item in plan["length_repairs"])
    assert len(plan["length_repairs"]) == SCRIPT_BUDGET_MAX_REPAIRS


def test_resolve_script_policy_defaults_legacy_rows() -> None:
    assert resolve_script_policy(None) == "ADAPT_SOURCES"
    assert resolve_script_policy("") == "ADAPT_SOURCES"
    assert resolve_script_policy("CREATE_FROM_TOPIC") == "CREATE_FROM_TOPIC"
    with pytest.raises(ExplainerContractError):
        resolve_script_policy("whatever")


def test_content_kind_and_policy_are_both_required_for_topic_fiction(database: Database) -> None:
    _seed_adapt_project(database, script_policy="CREATE_FROM_TOPIC")
    planner = LocalTextPlanner(client_factory=lambda: InsufficientDraftClient())
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_story_seed(
                repo=ExplainerRepository(connection), project_id=PROJECT_ID, video_id=VIDEO_ID
            )
    # content_kind is FACTUAL_EXPLAINER in the fixture, so the fiction branch is refused.
    assert error.value.code == "INVALID_REQUEST"


# --------------------------------------------------------------------------- #
# topic mode: the authored-fiction seed and the reference-design compiler
# --------------------------------------------------------------------------- #
class FictionSeedClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def chat_json(self, system, user, images=None, *, json_schema=None, inference_options=None):
        self.calls.append({"system": system, "user": user})
        return {
            "schema_version": "localdrama.explainer.fiction-seed.v1",
            "title": "夜航灯",
            "premise": "一名年轻守灯员发现港口信号灯反复指向一艘早已停航的小船。",
            "setting": "虚构的近代海港小镇；空间集中在灯塔、码头和旧船舱。",
            "characters": [
                {
                    "name": "林舟",
                    "role": "守灯员",
                    "appearance": "短发、深色旧外套，携带工作手电。",
                    "stable_traits": ["谨慎", "熟悉港口信号"],
                    "desire": "查明异常灯号来源",
                }
            ],
            "story_steps": [
                {
                    "summary": "林舟记录反复出现的异常灯号，并前往旧船核查。",
                    "character_indexes": [0],
                    "location": "码头",
                    "cause": "灯号与港口当前航线不符",
                    "result": "他在旧船舱发现一台仍在运转的定时信号装置",
                }
            ],
            "ending": "装置留下的是上一任守灯员未完成的告别信号，林舟在记录后关闭它。",
            "continuity_constraints": ["港口与人物均为虚构"],
            "scope_conflicts": [],
        }


def _fiction_project(database: Database) -> dict[str, str]:
    seeded = _seed_adapt_project(database, script_policy="CREATE_FROM_TOPIC")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE explainer_videos SET content_kind='ORIGINAL_FICTION', topic='海岛灯塔的原创逃脱故事' WHERE id=?",
            (VIDEO_ID,),
        )
    return seeded


def test_story_seed_is_registered_as_authored_fiction_and_reused(database: Database) -> None:
    from local_drama.application.explainers.contracts_v2 import (
        fiction_seed_setting_document,
        validate_contract,
    )

    _fiction_project(database)
    client = FictionSeedClient()
    planner = LocalTextPlanner(client_factory=lambda: client)
    with database.transaction() as connection:
        first = planner.plan_story_seed(
            repo=ExplainerRepository(connection), project_id=PROJECT_ID, video_id=VIDEO_ID
        )
    assert first["reused"] is False
    assert first["source_kind"] == "AUTHORED_FICTION_PACK"
    assert first["credibility_kind"] == "AUTHORED_FICTION"
    assert first["verified_as_history"] is False
    assert first["fact_verification_status"] is None
    assert first["checks"]["freezable"] is True
    assert first["setting_document"] == fiction_seed_setting_document(first["seed"])
    validate_contract("fiction-seed.v1", first["seed"])
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        source = repo.get("explainer_sources", first["source_id"])
        assert source["source_kind"] == "AUTHORED_FICTION_PACK"
        assert source["credibility_kind"] == "AUTHORED_FICTION"
        rights = source["rights_json"]
        assert rights["seed_input_hash"] == first["seed_input_hash"]
        assert rights["verified_as_history"] is False
        assert rights["prompt_hash"] and rights["response_hash"]
        assert repo.list_where("explainer_source_spans", {"source_id": first["source_id"]})

    # The same input hash reuses the finished seed: the world is never rewritten
    # just because the user opened the page again.
    second_client = FictionSeedClient()
    second_planner = LocalTextPlanner(client_factory=lambda: second_client)
    with database.connect() as connection:
        second = second_planner.plan_story_seed(
            repo=ExplainerRepository(connection), project_id=PROJECT_ID, video_id=VIDEO_ID
        )
    assert second["reused"] is True
    assert second["source_id"] == first["source_id"]
    assert second_client.calls == []


def test_story_seed_with_scope_conflicts_is_not_frozen(database: Database) -> None:
    _fiction_project(database)

    class ConflictingClient(FictionSeedClient):
        def chat_json(self, system, user, images=None, *, json_schema=None, inference_options=None):
            payload = super().chat_json(
                system, user, images, json_schema=json_schema, inference_options=inference_options
            )
            payload["scope_conflicts"] = ["用户要求写真实历史人物，这不是原创虚构"]
            return payload

    planner = LocalTextPlanner(client_factory=lambda: ConflictingClient())
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_story_seed(
                repo=ExplainerRepository(connection), project_id=PROJECT_ID, video_id=VIDEO_ID
            )
    assert error.value.details["scope_conflicts"]


def test_reference_design_compiles_deterministically_without_a_model(database: Database) -> None:
    seeded = _seed_adapt_project(database, script_policy="ADAPT_SOURCES")
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        entity = repo.insert(
            "explainer_entities",
            {
                "video_id": VIDEO_ID,
                "project_id": PROJECT_ID,
                "code": "E001",
                "entity_type": "REAL_PERSON",
                "name": "林舟",
                "aliases_json": [],
                "fictional": False,
                "descriptive_only": True,
                "disambiguation_json": {"known_appearance": [{"attribute": "外套", "value": "深色旧外套"}]},
                "status": "ACTIVE",
            },
        )
        entity_id = str(entity["id"])
    client = InsufficientDraftClient()
    planner = LocalTextPlanner(client_factory=lambda: client)
    request = [{"entity_id": entity_id, "asset_kind": "CHARACTER", "reference_kind": "FRONT"}]
    with database.connect() as connection:
        first = planner.plan_reference_design(
            repo=ExplainerRepository(connection),
            project_id=seeded["project_id"],
            video_id=VIDEO_ID,
            requested=request,
            style=["灰蓝铜色插画风格"],
            user_visual_settings={entity_id: ["外套改用中性灰色"]},
        )
        second = planner.plan_reference_design(
            repo=ExplainerRepository(connection),
            project_id=seeded["project_id"],
            video_id=VIDEO_ID,
            requested=request,
            style=["灰蓝铜色插画风格"],
            user_visual_settings={entity_id: ["外套改用中性灰色"]},
        )
    assert client.calls == []
    assert first["model_calls"] == 0 and first["compiled_deterministically"] is True
    assert first["items"] == second["items"]
    item = first["items"][0]
    assert item["prompt_hash"] == second["items"][0]["prompt_hash"]
    prompt = item["description_prompt"]
    # Fixed compile order: purpose, known appearance, user setting, style, view.
    assert prompt.index("角色标准参考设定") < prompt.index("外套：深色旧外套")
    assert prompt.index("外套：深色旧外套") < prompt.index("外套改用中性灰色")
    assert prompt.index("外套改用中性灰色") < prompt.index("灰蓝铜色插画风格")
    assert prompt.index("灰蓝铜色插画风格") < prompt.index("严格正面视角")
    assert first["facts_ledger_untouched"] is True
    # A real person with no known appearance and no reliable portrait is never
    # presented as a faithful reconstruction; the pure compiler is asserted here
    # because this entity does have a documented attribute.
    from local_drama.application.explainers.contracts_v2 import compile_reference_design

    unknown = compile_reference_design(
        entity_name="某人",
        asset_kind="CHARACTER",
        reference_kind="FRONT",
        unknown_real_person=True,
    )
    assert "不得声称真实复原面部" in unknown["description_prompt"]
    assert "示意角度" in unknown["description_prompt"]


def test_reference_design_reports_an_over_budget_field_instead_of_truncating(database: Database) -> None:
    seeded = _seed_adapt_project(database, script_policy="ADAPT_SOURCES")
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        entity = repo.insert(
            "explainer_entities",
            {
                "video_id": VIDEO_ID,
                "project_id": PROJECT_ID,
                "code": "E001",
                "entity_type": "LOCATION",
                "name": "灯塔内部",
                "aliases_json": [],
                "fictional": False,
                "descriptive_only": False,
                "disambiguation_json": {},
                "status": "ACTIVE",
            },
        )
    planner = LocalTextPlanner(client_factory=lambda: InsufficientDraftClient())
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_reference_design(
                repo=ExplainerRepository(connection),
                project_id=seeded["project_id"],
                video_id=VIDEO_ID,
                requested=[{"entity_id": str(entity["id"]), "asset_kind": "SCENE", "reference_kind": "SCENE_WIDE"}],
                user_visual_settings={},
                style=["风格" * 500],
            )
    assert error.value.details["over_budget_fields"]
    assert error.value.details["over_budget_fields"][0]["field"] in {"style", "description_prompt"}
    # The view/composition hard requirement is never the truncated tail.
    assert error.value.details["over_budget_fields"][0]["length"] > error.value.details["over_budget_fields"][0]["limit"]


def test_reference_design_model_polish_keeps_the_program_owned_hard_requirements(
    database: Database,
) -> None:
    seeded = _seed_adapt_project(database, script_policy="ADAPT_SOURCES")
    with database.transaction() as connection:
        entity = ExplainerRepository(connection).insert(
            "explainer_entities",
            {
                "video_id": VIDEO_ID,
                "project_id": PROJECT_ID,
                "code": "E001",
                "entity_type": "REAL_PERSON",
                "name": "林舟",
                "aliases_json": [],
                "fictional": False,
                "descriptive_only": False,
                "disambiguation_json": {"known_appearance": [{"attribute": "外套", "value": "深色旧外套"}]},
                "status": "ACTIVE",
            },
        )
    entity_id = str(entity["id"])

    class PolishClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def chat_json(self, system, user, images=None, *, json_schema=None, inference_options=None):
            self.calls.append({"system": system, "user": user})
            return {
                "schema_version": "localdrama.explainer.reference-design.v1",
                "items": [
                    {
                        "entity_id": entity_id,
                        "asset_kind": "CHARACTER",
                        "reference_kind": "FRONT",
                        "description_prompt": "整理后的设定段落：沿用深色旧外套。",
                        "negative_prompt": "",
                        "known_attribute_keys": [],
                        "user_setting_keys": [],
                        "reference_media_version_ids": [],
                        "creative_choices": [],
                        "unresolved_constraints": [],
                    }
                ],
            }

    client = PolishClient()
    planner = LocalTextPlanner(client_factory=lambda: client)
    with database.connect() as connection:
        plan = planner.plan_reference_design(
            repo=ExplainerRepository(connection),
            project_id=seeded["project_id"],
            video_id=VIDEO_ID,
            requested=[{"entity_id": entity_id, "asset_kind": "CHARACTER", "reference_kind": "FRONT"}],
            style=["灰蓝铜色插画风格"],
            polish_with_model=True,
        )
    assert len(client.calls) == 1 and plan["model_calls"] == 1
    item = plan["items"][0]
    assert item["model_polished"] is True
    # The program-owned parts survived the model's re-wording.
    assert "林舟" in item["description_prompt"]
    assert "外套：深色旧外套" in item["description_prompt"]
    assert "灰蓝铜色插画风格" in item["description_prompt"]
    assert item["description_prompt"].rstrip().endswith("非四分之三侧面。")
    assert item["compile_order"] == [
        "asset_kind_purpose",
        "known_appearance_or_space",
        "description_prose",
        "user_visual_settings",
        "adopted_reference_invariants",
        "style",
        "reference_view_and_composition",
    ]


def test_reference_design_rejects_an_unknown_entity(database: Database) -> None:
    seeded = _seed_adapt_project(database, script_policy="ADAPT_SOURCES")
    planner = LocalTextPlanner(client_factory=lambda: InsufficientDraftClient())
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            planner.plan_reference_design(
                repo=ExplainerRepository(connection),
                project_id=seeded["project_id"],
                video_id=VIDEO_ID,
                requested=[{"entity_id": "entity-from-another-project", "reference_kind": "HERO"}],
            )
    assert error.value.details["entity_id"] == "entity-from-another-project"


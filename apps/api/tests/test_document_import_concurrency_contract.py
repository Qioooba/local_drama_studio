"""A failed concurrent import must not delete the winner's extracted body (IMP-04).

Reproduced defect on the audit snapshot.  Two imports of the same file computed the
same content-addressed ``extracted_text_rel`` **and** the same fixed
``.partial`` name, and the losing request's ``except`` branch ran
``text_path.unlink()`` on that *published* path:

```json
{
  "successful_requests": 1,
  "failed_requests": 1,
  "loser_error": "UNIQUE constraint failed: source_documents.project_id, source_documents.code",
  "winner_session_status": "PREVIEW_READY",
  "winner_extracted_text_exists": false,
  "winner_get_paragraphs_error": "IMPORT_EXTRACTED_TEXT_CHANGED"
}
```

A database rollback cannot restore a deleted file, so the surviving session was
recorded as ready while its authoritative text was gone.

These tests assert the corrected invariants: a published shared object survives an
unrelated failure, a failing import cannot remove it, two writers stage under
distinct names, and only the failing request's own staged file is cleaned up.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from local_drama.application.documents import DocumentImportService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError

PROJECT_CODE = "import_concurrency_contract"


def _project(workspace, database) -> str:
    project = ProjectService(database, workspace.projects_root).create_project(
        code=PROJECT_CODE,
        title="Import concurrency",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1000,
        allow_unconfigured_capabilities=True,
    )
    return str(project["id"])


def _service(workspace, database) -> DocumentImportService:
    return DocumentImportService(database, workspace)


def test_two_writers_stage_under_distinct_names(workspace, database, tmp_path: Path) -> None:
    """The fixed ``.partial`` name was the second half of the defect."""

    service = _service(workspace, database)
    target = tmp_path / "extracted.txt"
    first = service._write_extracted_text(target, "同一段正文。")  # noqa: SLF001
    second = service._write_extracted_text(target, "另一段正文。")  # noqa: SLF001
    assert first != second
    assert first.name != second.name
    assert first.name.startswith(f".{target.name}.") and first.name.endswith(".partial")
    # Never the old fixed suffix, which two requests could both own.
    assert first.name != f"{target.name}.partial"
    # Whichever published last wins the shared object, and it is never truncated.
    assert target.read_text(encoding="utf-8") == "另一段正文。"


def test_an_identical_content_object_is_reused_not_replaced(workspace, database, tmp_path: Path) -> None:
    service = _service(workspace, database)
    target = tmp_path / "extracted.txt"
    published = service._write_extracted_text(target, "内容一致。")  # noqa: SLF001
    service._discard_partial(published)  # noqa: SLF001
    before = target.stat().st_mtime_ns
    staged = service._write_extracted_text(target, "内容一致。")  # noqa: SLF001
    assert staged.exists()  # the redundant copy is this request's own file
    assert target.stat().st_mtime_ns == before
    assert target.read_text(encoding="utf-8") == "内容一致。"


def test_discarding_a_partial_never_touches_the_published_object(workspace, database, tmp_path: Path) -> None:
    service = _service(workspace, database)
    target = tmp_path / "extracted.txt"
    staged = service._write_extracted_text(target, "权威正文。")  # noqa: SLF001
    service._discard_partial(staged)  # noqa: SLF001
    assert not staged.exists()
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "权威正文。"


def test_a_failed_second_import_keeps_the_first_import_readable(workspace, database) -> None:
    """The end-to-end invariant: the winner's paragraphs still load."""

    project_id = _project(workspace, database)
    service = _service(workspace, database)
    source = workspace.work_root / "novel.txt"
    source.write_text("第一段正文。\n\n第二段正文。", encoding="utf-8")

    first = service.import_document(project_id=project_id, source_path=source)
    assert first["status"] == "PREVIEW_READY"
    version_id = str(first["source_document_version_id"])
    session_id = str(first["import_session_id"])

    # A second, deliberately failing import in the same project.
    broken = workspace.work_root / "novel.bin"
    broken.write_bytes(b"\x00\x01\x02")
    try:
        service.import_document(project_id=project_id, source_path=broken)
    except DomainRuleError:
        pass

    paragraphs = service.get_paragraphs(session_id, start=1, limit=10)
    assert paragraphs["total_paragraph_count"] == 2
    assert paragraphs["items"][0]["text"] == "第一段正文。"

    with database.connect() as connection:
        row = connection.execute(
            "SELECT extracted_text_rel, text_sha256 FROM source_document_versions WHERE id=?",
            (version_id,),
        ).fetchone()
    root = service.media._project_root(project_id)  # noqa: SLF001
    published = root / str(row["extracted_text_rel"])
    assert published.is_file(), published
    assert hashlib.sha256(published.read_bytes()).hexdigest() == str(row["text_sha256"])


def test_staged_text_survives_a_deep_project_root(workspace, database) -> None:
    """The staging name must not push a real import past the Windows path limit.

    A full 32-character UUID token made the staged path 4 characters longer than
    the 260-character MAX_PATH boundary at exactly this directory depth, so the
    write failed with ``FileNotFoundError`` while the short published name worked.
    """

    project_id = _project(workspace, database)
    service = _service(workspace, database)
    root = service.media._project_root(project_id)  # noqa: SLF001
    # The real layout this broke on: the deepest directory the import writes into.
    deep = root / "00_admin" / "imports"
    deep.mkdir(parents=True, exist_ok=True)
    # 84-character published name, exactly like "<sha256>.<parser>.<structure>.extracted.txt".
    name = f"{'a' * 64}.{'b' * 8}.{'c' * 8}.extracted.txt"
    target = deep / name
    staged = service._write_extracted_text(target, "深层目录正文。")  # noqa: SLF001
    try:
        assert target.is_file()
        assert target.read_text(encoding="utf-8") == "深层目录正文。"
        # The staged name stays comfortably inside the legacy path limit.
        assert len(str(staged)) <= 259, len(str(staged))
        assert len(str(target)) <= 259, len(str(target))
    finally:
        service._discard_partial(staged)  # noqa: SLF001


def test_the_failure_path_never_deletes_the_published_authority() -> None:
    """Structural guard: the old failure branch unlinked the *published* path."""

    source = Path("apps/api/local_drama/application/documents.py").read_text(encoding="utf-8")
    assert "self._discard_partial(staged_text)" in source
    # The published path must never be unlinked from an import failure branch.
    assert "text_path.unlink(missing_ok=True)" not in source

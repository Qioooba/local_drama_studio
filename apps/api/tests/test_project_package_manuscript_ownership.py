"""Project-package manuscripts must be re-owned by the copy (PKG-01).

Reproduced defect on the audit snapshot.  ``source_document_versions.metadata_json``
was serialised verbatim, but ``metadata.media_version_id`` is a *runtime external
reference*, not descriptive text:

| scenario | actual result | user consequence |
|---|---|---|
| source project still in the same database | the copy's "latest manuscript" returned the **source** project's media_version_id | the copy still depends on the source project's media |
| package imported into a fresh database | ``import_as_copy`` returned ``IMPORTED``; reading the latest manuscript raised ``MEDIA_VERSION_NOT_FOUND`` | the file was copied but the manuscript still could not be opened |

The existing roundtrip test passed because it seeded ``metadata`` by hand without a
media id, so only row counts and hashes were checked.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from local_drama.application.documents import DocumentImportService
from local_drama.application.project_packages import ProjectPackageService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError


def _project(database, workspace, code: str) -> dict[str, Any]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )


def _import_document(database, workspace, project_id: str, name: str, body: str) -> dict[str, Any]:
    """Import *and commit* the manuscript.

    Only terminal import sessions travel inside a project package: a
    ``PREVIEW_READY`` session is UI working state and is listed under
    ``excluded_state`` instead of being replayed in the copy.  A committed session
    is the real business fact a copy has to restore.
    """

    source = workspace.work_root / name
    source.write_text(body, encoding="utf-8")
    service = DocumentImportService(database, workspace)
    imported = service.import_document(project_id=project_id, source_path=source)
    session = service.get_session(str(imported["import_session_id"]))
    service.commit(str(session["id"]), str(session["preview_hash"]), actor="tester")
    return imported


def _metadata_of(database, version_id: str) -> dict[str, Any]:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT metadata_json FROM source_document_versions WHERE id=?", (version_id,)
        ).fetchone()
    return json.loads(str(row["metadata_json"])) if row else {}


def _copy_project(database, workspace, source_project_id: str, export_name: str) -> tuple[str, dict[str, Any]]:
    packages = ProjectPackageService(database, workspace.projects_root)
    exported = packages.export(source_project_id)
    # ``export`` writes into the project's own exports directory; the inbox is the
    # controlled staging area the import path only ever reads from.
    source_archive = Path(str(exported["artifact"]["server_absolute_path"]))
    inbox = packages.staging_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    staged_name = f"{export_name}.ldspkg"
    (inbox / staged_name).write_bytes(source_archive.read_bytes())
    staged = packages.stage_from_inbox(staged_name)
    copy = packages.import_as_copy(
        str(staged["stage_token"]), code=f"{export_name}_copy", title=export_name, actor="tester"
    )
    return str(copy["project_id"]), copy


def test_a_real_import_writes_a_metadata_media_reference(database, workspace) -> None:
    """Establish the precondition: the metadata really holds a live media id."""

    project = _project(database, workspace, "pkg01_source")
    imported = _import_document(database, workspace, str(project["id"]), "novel.txt", "第一段正文。\n\n第二段正文。")
    metadata = _metadata_of(database, str(imported["source_document_version_id"]))
    assert metadata.get("media_version_id"), metadata
    assert metadata.get("paragraph_count") == 2


def test_a_copied_manuscript_points_at_the_copy_own_media(database, workspace) -> None:
    """The copy must not read the source project's media version."""

    source_project_id = str(_project(database, workspace, "pkg01_owner")["id"])
    imported = _import_document(database, workspace, source_project_id, "novel.txt", "第一段正文。\n\n第二段正文。")
    source_metadata = _metadata_of(database, str(imported["source_document_version_id"]))

    copied_project_id, _copy = _copy_project(database, workspace, source_project_id, "pkg01_owner")
    assert copied_project_id != source_project_id

    with database.connect() as connection:
        copied_version = connection.execute(
            "SELECT id, metadata_json FROM source_document_versions WHERE source_document_id IN"
            " (SELECT id FROM source_documents WHERE project_id=?)",
            (copied_project_id,),
        ).fetchone()
    assert copied_version is not None, "the copy has no manuscript version"
    copied_metadata = json.loads(str(copied_version["metadata_json"]))
    copied_media_id = copied_metadata.get("media_version_id")
    assert copied_media_id
    # The reference really moved, and the original is kept as provenance.
    assert copied_media_id != source_metadata["media_version_id"]
    assert copied_metadata.get("source_media_version_id") == source_metadata["media_version_id"]

    with database.connect() as connection:
        media = connection.execute(
            """SELECT mv.id, ma.project_id FROM media_versions mv
            JOIN media_assets ma ON ma.id = mv.media_asset_id WHERE mv.id = ?""",
            (copied_media_id,),
        ).fetchone()
    assert media is not None, "copied manuscript references a media version that does not exist"
    assert str(media["project_id"]) == copied_project_id

    # And the copy's own read path resolves it.
    latest = DocumentImportService(database, workspace).latest_for_project(copied_project_id)
    assert latest is not None
    assert str(latest["import"]["source_document_version_id"]) == str(copied_version["id"])
    assert str(latest["import"]["media_version_id"]) == copied_media_id


def test_the_copied_manuscript_body_matches_the_source(database, workspace) -> None:
    source_project_id = str(_project(database, workspace, "pkg01_body")["id"])
    _import_document(database, workspace, source_project_id, "novel.txt", "第一段正文。\n\n第二段正文。")
    copied_project_id, _copy = _copy_project(database, workspace, source_project_id, "pkg01_body")

    service = DocumentImportService(database, workspace)
    latest = service.latest_for_project(copied_project_id)
    assert latest is not None
    session = service.get_session(str(latest["import"]["import_session_id"]))
    page = service.get_paragraphs(str(session["id"]), start=1, limit=10)
    assert page["total_paragraph_count"] == 2
    assert [item["text"] for item in page["items"]] == ["第一段正文。", "第二段正文。"]

    version = session["source_document_version"]
    root = service.media._project_root(copied_project_id)  # noqa: SLF001
    copied_file = root / str(version["rel_path"])
    assert copied_file.is_file()
    assert hashlib.sha256(copied_file.read_bytes()).hexdigest() == str(version["sha256"])


def test_a_manuscript_media_reference_outside_the_package_is_refused(database, workspace) -> None:
    """A missing binding must be a named blocker, never a silent success."""

    source_project_id = str(_project(database, workspace, "pkg01_missing")["id"])
    imported = _import_document(database, workspace, source_project_id, "novel.txt", "唯一正文内容。")

    # Point the recorded metadata at a media version the package does not contain.
    with database.transaction() as connection:
        metadata = _metadata_of(database, str(imported["source_document_version_id"]))
        metadata["media_version_id"] = "media-version-not-in-package"
        connection.execute(
            "UPDATE source_document_versions SET metadata_json=? WHERE id=?",
            (json.dumps(metadata, ensure_ascii=False, sort_keys=True), str(imported["source_document_version_id"])),
        )

    packages = ProjectPackageService(database, workspace.projects_root)
    exported = packages.export(source_project_id)
    inbox = packages.staging_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "pkg01_missing.ldspkg").write_bytes(Path(str(exported["artifact"]["server_absolute_path"])).read_bytes())
    staged = packages.stage_from_inbox("pkg01_missing.ldspkg")
    with pytest.raises(DomainRuleError) as error:
        packages.import_as_copy(str(staged["stage_token"]), code="pkg01_missing_copy", title="bad", actor="tester")
    assert error.value.code == "PROJECT_PACKAGE_MANUSCRIPT_REFERENCE_INVALID"
    assert "media-version-not-in-package" in json.dumps(error.value.details, ensure_ascii=False)



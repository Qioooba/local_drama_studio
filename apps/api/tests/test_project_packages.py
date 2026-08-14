from __future__ import annotations

import base64
import hashlib
import json
import shutil
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.project_packages import ProjectPackageService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _project(workspace, database) -> dict[str, object]:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(code="package_source", title="项目包源", episode_count=2, aspect_ratio="9:16",
        fps_num=24, fps_den=1, target_duration_ms=90_000, allow_unconfigured_capabilities=True)
    episode = projects.list_episodes(projects.list_seasons(str(project["id"]))[0]["id"])[0]
    shot = projects.create_shot(str(episode["id"]), "SHOT_001", 4_000, "CLOSE_UP")
    projects.create_shot_revision(str(shot["id"]), {"subject_action": "turn", "dialogue": "本地"}, freeze=True)
    root = workspace.projects_root / "package_source"
    sample = root / "01_story" / "source_documents" / "中文 剧本.md"
    sample.write_text("# 本地项目包\n", encoding="utf-8")
    return project


def _registered_image(workspace, database, project_id: str) -> tuple[str, str]:
    content = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
    relative = "04_media/images/assets/imported.png"
    path = workspace.projects_root / "package_source" / relative
    path.write_bytes(content)
    asset_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
    with database.transaction() as connection:
        shot_id = connection.execute(
            "SELECT sh.id FROM shots sh JOIN episodes e ON e.id=sh.episode_id JOIN seasons s ON s.id=e.season_id WHERE s.project_id=?",
            (project_id,),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO media_assets (id,project_id,owner_type,owner_id,purpose,media_kind,version_counter,metadata_json)
            VALUES (?,?,'SHOT',?,'KEYFRAME','IMAGE',1,'{}')""",
            (asset_id, project_id, shot_id),
        )
        connection.execute(
            """INSERT INTO media_versions (id,media_asset_id,version_no,take_no,stage,rel_path,mime_type,byte_size,sha256,
            integrity_status,source_name,import_source,probe_json) VALUES (?,?,1,1,'KEYFRAME',?,'image/png',?,?,'VERIFIED','imported.png','LOCAL_FILE','{}')""",
            (version_id, asset_id, relative, len(content), hashlib.sha256(content).hexdigest()),
        )
    return asset_id, version_id


def test_project_package_export_is_verified_deterministic_and_database_read_only(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root)
    with database.connect() as connection:
        audit_before = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    first = service.export(str(project["id"]))
    second = service.export(str(project["id"]))
    assert first["status"] == "EXPORTED" and first["reused"] is False
    assert second["reused"] is True and second["sha256"] == first["sha256"]
    assert first["database_mutated"] is False and first["network_contacted"] is False
    inspected = service.dry_run(str(project["id"]), str(first["rel_path"]))
    assert inspected["status"] == "READY_REBIND_EXISTING"
    assert inspected["conflict_options"] == ["REBIND_EXISTING", "IMPORT_AS_COPY_REWRITE_IDENTITY"]
    assert inspected["would_import"] is False and inspected["mutated"] is False
    package = workspace.projects_root / "package_source" / str(first["rel_path"])
    with zipfile.ZipFile(package) as archive:
        assert "payload/01_story/source_documents/中文 剧本.md" in archive.namelist()
        assert "package-manifest.json" in archive.namelist()
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == audit_before


def test_project_package_dry_run_rejects_zip_slip_duplicates_and_unregistered_paths(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root)
    output = workspace.projects_root / "package_source" / "exports" / "project-packages"
    output.mkdir(parents=True, exist_ok=True)
    malicious = output / "malicious.ldspkg"
    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr("../escape", b"bad")
        archive.writestr("package-manifest.json", b"{}")
        archive.writestr("project-state.json", b"{}")
    with pytest.raises(DomainRuleError) as unsafe:
        service.dry_run(str(project["id"]), "exports/project-packages/malicious.ldspkg")
    assert unsafe.value.code == "PROJECT_PACKAGE_PATH_INVALID"
    outside = workspace.projects_root / "outside.ldspkg"
    outside.write_bytes(b"not a package")
    with pytest.raises(DomainRuleError) as path_error:
        service.dry_run(str(project["id"]), "../outside.ldspkg")
    assert path_error.value.code == "PROJECT_PACKAGE_PATH_NOT_ALLOWED"


def test_project_package_api_exports_and_dry_runs_only_registered_package(workspace, database) -> None:
    project = _project(workspace, database)
    with TestClient(create_app(workspace)) as client:
        exported = client.post(f"/api/v1/projects/{project['id']}/packages:export")
        assert exported.status_code == 200
        package = exported.json()["package"]
        inspected = client.post(f"/api/v1/projects/{project['id']}/packages:dry-run", json={"rel_path": package["rel_path"]})
    assert inspected.status_code == 200
    assert inspected.json()["dry_run"]["status"] == "READY_REBIND_EXISTING"


def test_external_package_staging_uses_fixed_inbox_and_content_addressed_token(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(str(project["id"]))
    source = workspace.projects_root / "package_source" / str(exported["rel_path"])
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True)
    shutil.copy2(source, inbox / "外部 项目.ldspkg")
    staged = service.stage_from_inbox("外部 项目.ldspkg")
    repeated = service.stage_from_inbox("外部 项目.ldspkg")
    assert staged["status"] == "STAGED" and staged["source_retained"] is True
    assert staged["stage_token"] == exported["sha256"] and repeated["reused"] is True
    assert service.dry_run_staged(str(staged["stage_token"]))["status"] == "READY_REBIND_EXISTING"
    assert (inbox / "外部 项目.ldspkg").is_file()
    assert not list((workspace.data_root / "imports" / "project-packages" / "staged").glob(".partial-*"))
    with pytest.raises(DomainRuleError) as traversal:
        service.stage_from_inbox("../外部 项目.ldspkg")
    assert traversal.value.code == "PROJECT_PACKAGE_INBOX_NAME_INVALID"


def test_external_package_staging_api_never_accepts_arbitrary_paths(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(str(project["id"]))
    source = workspace.projects_root / "package_source" / str(exported["rel_path"])
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True)
    shutil.copy2(source, inbox / "api.ldspkg")
    with TestClient(create_app(workspace)) as client:
        staged = client.post("/api/v1/project-packages:stage", json={"inbox_name": "api.ldspkg"})
        assert staged.status_code == 200
        token = staged.json()["staging"]["stage_token"]
        dry_run = client.post(f"/api/v1/project-packages/{token}:dry-run")
        rejected = client.post("/api/v1/project-packages:stage", json={"inbox_name": str(source)})
    assert dry_run.status_code == 200 and dry_run.json()["dry_run"]["would_import"] is False
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "PROJECT_PACKAGE_INBOX_NAME_INVALID"


def test_staged_project_package_import_as_copy_rewrites_identity_and_retains_retry_source(workspace, database) -> None:
    source_project = _project(workspace, database)
    _registered_image(workspace, database, str(source_project["id"]))
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(str(source_project["id"]))
    source = workspace.projects_root / "package_source" / str(exported["rel_path"])
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True)
    shutil.copy2(source, inbox / "copy.ldspkg")
    token = str(service.stage_from_inbox("copy.ldspkg")["stage_token"])

    imported = service.import_as_copy(token, code="package_copy", title="项目包副本")
    repeated = service.import_as_copy(token, code="package_copy", title="项目包副本")

    assert imported["status"] == "IMPORTED"
    assert repeated["reused"] is True and repeated["project_id"] == imported["project_id"]
    assert imported["identity_mode"] == "IMPORT_AS_COPY_REWRITE_IDENTITY"
    assert imported["project_id"] != source_project["id"]
    assert imported["counts"]["episodes"] == 2
    assert imported["counts"]["shots"] == 1
    assert imported["counts"]["media_assets"] == 1
    assert imported["counts"]["media_versions"] == 1
    assert imported["counts"]["thumbnails_pending"] == 1
    assert (workspace.data_root / "imports" / "project-packages" / "staged" / f"{token}.ldspkg").is_file()
    copied_root = workspace.projects_root / "package_copy"
    assert (copied_root / "01_story" / "source_documents" / "中文 剧本.md").read_text(encoding="utf-8") == "# 本地项目包\n"
    project_json = json.loads((copied_root / "project.json").read_text(encoding="utf-8"))
    assert project_json["project_id"] == imported["project_id"]
    assert project_json["project_code"] == "package_copy"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects WHERE id=?", (imported["project_id"],)).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE s.project_id=?", (imported["project_id"],)
        ).fetchone()[0] == 2
        imported_media = connection.execute(
            """SELECT ma.owner_id,mv.id AS media_version_id,mv.rel_path,mv.sha256 FROM media_assets ma JOIN media_versions mv ON mv.media_asset_id=ma.id
            JOIN shots sh ON sh.id=ma.owner_id JOIN episodes e ON e.id=sh.episode_id JOIN seasons s ON s.id=e.season_id
            WHERE ma.project_id=? AND s.project_id=?""",
            (imported["project_id"], imported["project_id"]),
        ).fetchone()
        assert imported_media is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM media_cache_entries mce JOIN media_versions mv ON mv.id=mce.media_version_id WHERE mv.id IN (SELECT mv2.id FROM media_versions mv2 JOIN media_assets ma ON ma.id=mv2.media_asset_id WHERE ma.project_id=?)",
            (imported["project_id"],),
        ).fetchone()[0] == 0
        audit = connection.execute(
            "SELECT action FROM audit_events WHERE subject_id=? ORDER BY rowid DESC LIMIT 1", (imported["project_id"],)
        ).fetchone()
        assert connection.execute("SELECT COUNT(*) FROM project_package_imports WHERE status='COMPLETED'").fetchone()[0] == 1
    assert audit["action"] == "PROJECT_PACKAGE_IMPORTED"
    assert hashlib.sha256((copied_root / imported_media["rel_path"]).read_bytes()).hexdigest() == imported_media["sha256"]
    thumbnail, mime = MediaService(database, workspace).thumbnail(str(imported_media["media_version_id"]), "small")
    assert thumbnail.is_file() and thumbnail.stat().st_size > 0 and mime == "image/webp"
    assert "small" in thumbnail.parts


def test_staged_project_package_import_rolls_back_database_and_filesystem_on_failure(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(str(project["id"]))
    source = workspace.projects_root / "package_source" / str(exported["rel_path"])
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True)
    shutil.copy2(source, inbox / "rollback.ldspkg")
    token = str(service.stage_from_inbox("rollback.ldspkg")["stage_token"])

    with pytest.raises(RuntimeError, match="simulated"):
        service.import_as_copy(token, code="package_rollback", title="回滚", simulate_failure=True)

    assert not (workspace.projects_root / "package_rollback").exists()
    assert not list(workspace.projects_root.glob(".package_rollback.import-*"))
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects WHERE code='package_rollback'").fetchone()[0] == 0
        receipt = connection.execute(
            "SELECT target_project_id,status FROM project_package_imports WHERE target_code='package_rollback'"
        ).fetchone()
    assert receipt["status"] == "FAILED"
    assert (workspace.data_root / "imports" / "project-packages" / "staged" / f"{token}.ldspkg").is_file()

    retried = service.import_as_copy(token, code="package_rollback", title="回滚后重试")
    assert retried["project_id"] == receipt["target_project_id"] and retried["reused"] is False


def test_rebind_existing_only_restores_a_missing_matching_project_root(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(str(project["id"]))
    source = workspace.projects_root / "package_source" / str(exported["rel_path"])
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True)
    shutil.copy2(source, inbox / "rebind.ldspkg")
    token = str(service.stage_from_inbox("rebind.ldspkg")["stage_token"])

    with pytest.raises(DomainRuleError) as overwrite:
        service.rebind_existing(token)
    assert overwrite.value.code == "PROJECT_PACKAGE_REBIND_ROOT_EXISTS"

    shutil.rmtree(workspace.projects_root / "package_source")
    rebound = service.rebind_existing(token)
    assert rebound["status"] == "REBOUND" and rebound["database_structure_changed"] is False
    root = workspace.projects_root / "package_source"
    assert (root / "01_story" / "source_documents" / "中文 剧本.md").is_file()
    assert json.loads((root / "project.json").read_text(encoding="utf-8"))["project_id"] == project["id"]


def test_project_package_commit_api_requires_explicit_identity_decision(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(str(project["id"]))
    source = workspace.projects_root / "package_source" / str(exported["rel_path"])
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True)
    shutil.copy2(source, inbox / "commit.ldspkg")
    token = str(service.stage_from_inbox("commit.ldspkg")["stage_token"])
    with TestClient(create_app(workspace)) as client:
        missing_identity = client.post(
            f"/api/v1/project-packages/{token}:commit",
            json={"identity_mode": "IMPORT_AS_COPY_REWRITE_IDENTITY"},
        )
        committed = client.post(
            f"/api/v1/project-packages/{token}:commit",
            json={"identity_mode": "IMPORT_AS_COPY_REWRITE_IDENTITY", "code": "api_package_copy", "title": "API 项目包副本"},
        )
    assert missing_identity.status_code == 422
    assert missing_identity.json()["error"]["code"] == "PROJECT_PACKAGE_COPY_IDENTITY_REQUIRED"
    assert committed.status_code == 201
    assert committed.json()["commit"]["project_code"] == "api_package_copy"


def test_stale_import_journal_recovers_only_its_owned_orphan_root(workspace, database) -> None:
    project = _project(workspace, database)
    service = ProjectPackageService(database, workspace.projects_root, workspace.data_root)
    exported = service.export(str(project["id"]))
    source = workspace.projects_root / "package_source" / str(exported["rel_path"])
    inbox = workspace.data_root / "imports" / "project-packages" / "inbox"
    inbox.mkdir(parents=True)
    shutil.copy2(source, inbox / "crash.ldspkg")
    token = str(service.stage_from_inbox("crash.ldspkg")["stage_token"])
    mode, code = "IMPORT_AS_COPY_REWRITE_IDENTITY", "crash_recovery"
    operation_key = hashlib.sha256(f"{token}\0{mode}\0{code}".encode()).hexdigest()
    target_project_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO project_package_imports (operation_key,stage_token,identity_mode,source_project_id,
            target_project_id,target_code,status,result_json,created_at,updated_at,created_by)
            VALUES (?,?,?,?,?,?,'PREPARING','{}','2000-01-01T00:00:00Z','2000-01-01T00:00:00Z','test')""",
            (operation_key, token, mode, project["id"], target_project_id, code),
        )
    orphan = workspace.projects_root / code
    orphan.mkdir()
    (orphan / "project.json").write_text(json.dumps({
        "project_id": target_project_id, "project_code": code, "imported_from_package_sha256": token,
    }), encoding="utf-8")
    (orphan / "orphan.tmp").write_text("partial", encoding="utf-8")

    recovered = service.import_as_copy(token, code=code, title="崩溃恢复")

    assert recovered["project_id"] == target_project_id
    assert not (orphan / "orphan.tmp").exists()
    assert (orphan / "01_story" / "source_documents" / "中文 剧本.md").is_file()

from __future__ import annotations

import zipfile

import pytest
from fastapi.testclient import TestClient

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

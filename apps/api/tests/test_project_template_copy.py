from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from local_drama.application.configuration import ConfigurationService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def _source(workspace, database) -> tuple[ProjectService, dict[str, object]]:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="template_source", title="Template source", episode_count=2, aspect_ratio="9:16",
        fps_num=24, fps_den=1, target_duration_ms=90_000, allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(projects.list_seasons(str(project["id"]))[0]["id"])[0]
    shot = projects.create_shot(str(episode["id"]), "SHOT_001", 4_000, "MEDIUM")
    projects.create_shot_revision(str(shot["id"]), {"subject_action": "walk", "prompt": "reusable structure"}, freeze=True)
    return projects, project


def test_copy_project_template_clones_only_reusable_structure_and_configuration(workspace, database) -> None:
    projects, source = _source(workspace, database)
    source_id = str(source["id"])
    configuration = ConfigurationService(database)
    configuration.create_plan_binding(source_id, "source-plan", "Source plan", {"mode": "LOCAL_ONLY", "fps": "24/1"})
    configuration.create_delivery_target(source_id, "master", "Master", "LOCAL_FILESYSTEM", {"path_rel": "06_delivery/master"})
    profile_service = ProfileService(database, workspace.manifest_path)
    profile_service.sync_manifest()
    profile = profile_service.list_profiles()[0]
    with database.transaction() as connection:
        connection.execute("UPDATE execution_profile_versions SET status='PUBLISHED' WHERE id=?", (profile["version_id"],))
        connection.execute(
            """INSERT INTO media_assets (id, project_id, owner_type, owner_id, purpose, media_kind, created_at, updated_at, created_by)
            VALUES ('source-media', ?, 'PROJECT', ?, 'CHARACTER_REFERENCE', 'IMAGE', 'now', 'now', 'test')""",
            (source_id, source_id),
        )
    configuration.bind_profile(source_id, str(profile["capability"]), str(profile["version_id"]))
    source_shot_id = str(projects.list_shots(str(projects.list_episodes(projects.list_seasons(source_id)[0]["id"])[0]["id"]))[0]["id"])

    result = projects.copy_as_template(source_id, code="template_copy", title="Template copy")
    copied = result["project"]
    report = result["copy_report"]
    assert copied["status"] == "DRAFT"
    assert copied["aspect_ratio"] == "9:16"
    assert report["copied"] == {"seasons": 1, "episodes": 2, "scenes": 0, "shots": 1, "profiles": 1, "delivery_targets": 1}
    assert {"media", "reviews", "jobs", "brand_kits"}.issubset(report["excluded"])
    copied_season = projects.list_seasons(str(copied["id"]))[0]
    copied_episodes = projects.list_episodes(str(copied_season["id"]))
    copied_shot = projects.list_shots(str(copied_episodes[0]["id"]))[0]
    assert copied_shot["id"] != source_shot_id
    assert copied_shot["status"] == "DRAFT"
    with database.connect() as connection:
        revision = connection.execute("SELECT * FROM shot_revisions WHERE id=?", (copied_shot["current_revision_id"],)).fetchone()
        assert revision["is_frozen"] == 0
        assert json.loads(revision["fields_json"])["subject_action"] == "walk"
        assert connection.execute("SELECT COUNT(*) FROM media_assets WHERE project_id=?", (copied["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM project_plan_bindings WHERE project_id=?", (copied["id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM project_profile_bindings WHERE project_id=?", (copied["id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM delivery_targets WHERE project_id=?", (copied["id"],)).fetchone()[0] == 1


def test_copy_project_template_rolls_back_database_and_filesystem(workspace, database) -> None:
    projects, source = _source(workspace, database)
    with database.connect() as connection:
        audit_count = connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='PROJECT_TEMPLATE_COPIED'").fetchone()[0]
    with pytest.raises(RuntimeError, match="simulated project template copy failure"):
        projects.copy_as_template(str(source["id"]), code="template_rollback", title="Rollback", simulate_failure=True)
    assert not (workspace.projects_root / "template_rollback").exists()
    with database.connect() as connection:
        assert connection.execute("SELECT 1 FROM projects WHERE code='template_rollback'").fetchone() is None
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='PROJECT_TEMPLATE_COPIED'").fetchone()[0] == audit_count


def test_copy_project_template_api_reports_policy_and_rejects_duplicate_code(workspace, database) -> None:
    _, source = _source(workspace, database)
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v1/projects/{source['id']}:copy-template",
            json={"code": "api_template_copy", "title": "API template copy"},
        )
        duplicate = client.post(
            f"/api/v1/projects/{source['id']}:copy-template",
            json={"code": "api_template_copy", "title": "Duplicate"},
        )
    assert response.status_code == 201
    assert response.json()["copy_report"]["source_project_id"] == source["id"]
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "PROJECT_CODE_EXISTS"
    assert len(list(workspace.projects_root.glob("api_template_copy*"))) == 1

from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.profiles import ProfileService
from local_drama.main import create_app


def test_effective_configuration_resolves_profile_defaults_and_run_overrides(workspace, database) -> None:
    synced = ProfileService(database, workspace.manifest_path).sync_manifest()
    candidate = next(item for item in synced["profiles"] if str(item["capability"]).startswith("VIDEO_"))
    with database.transaction() as connection:
        connection.execute("UPDATE execution_profile_versions SET status='PUBLISHED' WHERE id=?", (candidate["version_id"],))
        project_id = "project-effective-config"
        connection.execute(
            "INSERT INTO projects (id, code, title, status, template_version, root_rel, created_at, updated_at, created_by, revision, schema_version) VALUES (?, 'effective', 'Effective Config', 'ACTIVE', 'v2', 'projects/effective', 'now', 'now', 'test', 1, 'v2')",
            (project_id,),
        )

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            "/api/v1/generation/effective-configuration:resolve",
            json={
                "project_id": project_id,
                "capability_code": candidate["capability"],
                "requested_profile_version_id": candidate["version_id"],
                "run_overrides": {"sigma_points": 24, "acceleration": "OFF"},
            },
        )
    assert response.status_code == 200
    configuration = response.json()["configuration"]
    assert configuration["profile_version_id"] == candidate["version_id"]
    assert configuration["effective_settings"]["sigma_points"] == 24
    assert configuration["setting_sources"]["sigma_points"] == "RUN_OVERRIDE"
    assert configuration["fingerprint"].startswith("sha256:")
    assert configuration["read_only"] is True
    assert configuration["local_only"] is True


def test_effective_configuration_rejects_unknown_override(workspace, database) -> None:
    synced = ProfileService(database, workspace.manifest_path).sync_manifest()
    candidate = next(item for item in synced["profiles"] if str(item["capability"]).startswith("VIDEO_"))
    with database.transaction() as connection:
        connection.execute("UPDATE execution_profile_versions SET status='PUBLISHED' WHERE id=?", (candidate["version_id"],))
        connection.execute(
            "INSERT INTO projects (id, code, title, status, template_version, root_rel, created_at, updated_at, created_by, revision, schema_version) VALUES ('project-effective-invalid', 'effective-invalid', 'Effective Invalid', 'ACTIVE', 'v2', 'projects/effective-invalid', 'now', 'now', 'test', 1, 'v2')",
        )

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            "/api/v1/generation/effective-configuration:resolve",
            json={
                "project_id": "project-effective-invalid",
                "capability_code": candidate["capability"],
                "requested_profile_version_id": candidate["version_id"],
                "run_overrides": {"not_declared": 1},
            },
        )
    assert response.status_code == 200
    configuration = response.json()["configuration"]
    assert any(item["code"] == "GENERATION_PREFERENCE_SETTING_UNKNOWN" for item in configuration["blocking_errors"])

from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def _payload(code: str, accept_blockers: bool = True) -> dict[str, object]:
    return {"code": code, "title": "计划项目", "episode_count": 60, "target_duration_ms": 90_000,
            "aspect_ratio": "9:16", "fps": {"numerator": 24, "denominator": 1}, "width": 1080, "height": 1920,
            "primary_language": "zh-CN", "subtitle_mode": "SIDECAR", "subtitle_language": "zh-CN",
            "allow_unconfigured_capabilities": accept_blockers}


def test_creation_plan_is_read_only_and_requires_explicit_blocker_acceptance(workspace, database) -> None:
    service = ProjectService(database, workspace.projects_root)
    before_size = database.path.stat().st_size
    accepted = service.plan_project_creation(code="planned_project", title="Planned", episode_count=60, aspect_ratio="9:16",
        fps_num=24, fps_den=1, target_duration_ms=90_000, allow_unconfigured_capabilities=True)
    blocked = service.plan_project_creation(code="blocked_project", title="Blocked", episode_count=1, aspect_ratio="9:16",
        fps_num=24, fps_den=1, target_duration_ms=90_000, allow_unconfigured_capabilities=False,
        width=1080, height=1920, primary_language="zh-CN", subtitle_mode="SIDECAR", subtitle_language="zh-CN")
    assert accepted["status"] == "READY_WITH_CONFIGURATION_BLOCKERS"
    assert accepted["configuration_blockers"] == ["PROFILE_NOT_BOUND", "PRODUCTION_PLAN_NOT_BOUND", "DELIVERY_TARGET_NOT_BOUND"]
    assert accepted["mutated"] is False and accepted["network_contacted"] is False
    assert blocked["status"] == "BLOCKED"
    assert blocked["blockers"] == accepted["configuration_blockers"]
    assert database.path.stat().st_size == before_size
    assert not (workspace.projects_root / "planned_project").exists()


def test_creation_plan_api_detects_database_and_directory_conflicts_without_writes(workspace, database) -> None:
    ProjectService(database, workspace.projects_root).create_project(code="existing_project", title="Existing", episode_count=1,
        aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True)
    orphan = workspace.projects_root / "orphan_project"
    orphan.mkdir()
    with TestClient(create_app(workspace)) as client:
        existing = client.post("/api/v1/projects:plan", json=_payload("existing_project"))
        orphaned = client.post("/api/v1/projects:plan", json=_payload("orphan_project"))
    assert existing.status_code == 200 and existing.json()["plan"]["status"] == "BLOCKED"
    assert "PROJECT_CODE_AVAILABLE" in existing.json()["plan"]["blockers"]
    assert orphaned.status_code == 200 and "PROJECT_ROOT_AVAILABLE" in orphaned.json()["plan"]["blockers"]
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1


def test_configured_creation_atomically_binds_plan_published_profile_and_local_target(workspace, database) -> None:
    profiles = ProfileService(database, workspace.manifest_path)
    profiles.sync_manifest()
    profile = profiles.list_profiles()[0]
    with database.transaction() as connection:
        connection.execute("UPDATE execution_profile_versions SET status='PUBLISHED' WHERE id=?", (profile["version_id"],))
    payload = {
        "code": "configured_create", "title": "完整配置项目", "season_count": 2, "episode_count": 3,
        "target_duration_ms": 90_000, "aspect_ratio": "16:9", "width": 1920, "height": 1080,
        "fps": {"numerator": 25, "denominator": 1}, "primary_language": "zh-CN",
        "subtitle_mode": "SIDECAR", "subtitle_language": "zh-CN", "allow_unconfigured_capabilities": False,
        "production_plan": {"code": "configured-create-plan", "title": "完整配置方案", "plan": {"mode": "LOCAL_ONLY", "fps": "25/1"}},
        "profile_bindings": [{"capability": profile["capability"], "profile_version_id": profile["version_id"]}],
        "delivery_target": {"code": "local-master", "title": "本地母版", "spec": {"path_rel": "06_delivery/master"}},
    }
    with TestClient(create_app(workspace)) as client:
        planned = client.post("/api/v1/projects:plan", json=payload)
        created = client.post("/api/v1/projects", json=payload)
    assert planned.status_code == 200 and planned.json()["plan"]["status"] == "READY"
    assert planned.json()["plan"]["configuration_blockers"] == []
    assert planned.json()["plan"]["structure"]["total_episode_count"] == 6
    assert created.status_code == 201, created.text
    project_id = created.json()["project"]["id"]
    assert created.json()["blockers"] == []
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM seasons WHERE project_id=?", (project_id,)).fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM project_profile_bindings WHERE project_id=?", (project_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM project_plan_bindings WHERE project_id=?", (project_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM delivery_targets WHERE project_id=?", (project_id,)).fetchone()[0] == 1


def test_configured_creation_rejects_profile_mismatch_before_creating_directory(workspace, database) -> None:
    payload = _payload("bad_profile", accept_blockers=False)
    payload["production_plan"] = {"code": "bad-profile-plan", "title": "Bad", "plan": {}}
    payload["profile_bindings"] = [{"capability": "video.proxy", "profile_version_id": "missing"}]
    payload["delivery_target"] = {"code": "local", "title": "Local", "spec": {"path_rel": "06_delivery/local"}}
    with TestClient(create_app(workspace)) as client:
        response = client.post("/api/v1/projects", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROFILE_CAPABILITY_INVALID"
    assert not (workspace.projects_root / "bad_profile").exists()

from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def _payload(code: str, accept_blockers: bool = True) -> dict[str, object]:
    return {"code": code, "title": "计划项目", "episode_count": 60, "target_duration_ms": 90_000,
            "aspect_ratio": "9:16", "fps": {"numerator": 24, "denominator": 1},
            "allow_unconfigured_capabilities": accept_blockers}


def test_creation_plan_is_read_only_and_requires_explicit_blocker_acceptance(workspace, database) -> None:
    service = ProjectService(database, workspace.projects_root)
    before_size = database.path.stat().st_size
    accepted = service.plan_project_creation(code="planned_project", title="Planned", episode_count=60, aspect_ratio="9:16",
        fps_num=24, fps_den=1, target_duration_ms=90_000, allow_unconfigured_capabilities=True)
    blocked = service.plan_project_creation(code="blocked_project", title="Blocked", episode_count=1, aspect_ratio="9:16",
        fps_num=24, fps_den=1, target_duration_ms=90_000, allow_unconfigured_capabilities=False)
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

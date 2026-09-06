from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from local_drama.application.configuration import ConfigurationService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def test_project_configuration_snapshot_is_read_only_and_exposes_local_switch_impact(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="configuration_impact", title="Configuration impact", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    ConfigurationService(database).create_plan_binding(project_id, "plan", "Plan", {"fps": "24/1", "mode": "LOCAL_ONLY"})
    target = ConfigurationService(database).create_delivery_target(project_id, "master", "Master", "LOCAL_FILESYSTEM", {"path_rel": "06_delivery/master"})
    before = database.path.stat().st_size
    snapshot = ConfigurationService(database).inspect_project_configuration(project_id)
    assert snapshot["production_plan"]["status"] == "ACTIVE"
    assert snapshot["selected_delivery_target_version_id"] == target["version_id"]
    assert snapshot["impact"]["profile_switches_preserve_frozen_jobs"] is True
    assert snapshot["impact"]["remote_transport_allowed"] is False
    assert snapshot["runtime_contacted"] is False
    assert snapshot["network_contacted"] is False
    assert snapshot["mutated"] is False
    assert database.path.stat().st_size == before
    with TestClient(create_app(workspace)) as client:
        response = client.get(f"/api/v1/projects/{project_id}/configuration")
    assert response.status_code == 200
    assert response.json()["configuration"]["project"]["id"] == project_id


def test_project_configuration_snapshot_rejects_unknown_project(workspace, database) -> None:
    with pytest.raises(DomainRuleError) as error:
        ConfigurationService(database).blockers("missing")
    assert error.value.code == "PROJECT_NOT_FOUND"
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/projects/missing/configuration")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROJECT_NOT_FOUND"


def test_creating_or_selecting_a_target_keeps_one_project_wide_current_version(workspace, database) -> None:
    """Create is the user's choice, so activation must not require a second click."""
    project = ProjectService(database, workspace.projects_root).create_project(
        code="target_selection_project", title="Target selection", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    service = ConfigurationService(database)
    first = service.create_delivery_target(project_id, "first-target", "First", "LOCAL_FILESYSTEM", {"path_rel": "06_delivery/first"})
    second = service.create_delivery_target(project_id, "second-target", "Second", "LOCAL_FILESYSTEM", {"path_rel": "06_delivery/second"})
    after_create = service.inspect_project_configuration(project_id)
    assert after_create["selected_delivery_target_version_id"] == second["version_id"]
    selected = service.select_delivery_target_version(project_id, first["version_id"])
    assert selected["version_id"] == first["version_id"]
    resolved = service.inspect_project_configuration(project_id)
    assert resolved["selected_delivery_target_version_id"] == first["version_id"]
    newer_second = service.create_delivery_target_version(
        project_id,
        second["id"],
        {"path_rel": "06_delivery/second-v2", "width": 1080, "height": 1920, "fps": 30},
        title="Second vertical",
    )
    after_new_version = service.inspect_project_configuration(project_id)
    assert after_new_version["selected_delivery_target_version_id"] == newer_second["version_id"]
    with database.connect() as connection:
        active = connection.execute(
            "SELECT COUNT(*) FROM delivery_target_versions WHERE delivery_target_id IN "
            "(SELECT id FROM delivery_targets WHERE project_id=?) AND status='ACTIVE'",
            (project_id,),
        ).fetchone()[0]
    assert active == 1


def test_reusing_a_project_target_code_creates_an_active_version_instead_of_a_duplicate(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="target_code_reuse", title="Target code reuse", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    service = ConfigurationService(database)
    first = service.create_delivery_target(
        project_id, "custom-render", "自定义规格", "LOCAL_FILESYSTEM",
        {"path_rel": "06_delivery/custom-render", "width": 480, "height": 854, "fps": 24},
    )
    second = service.create_delivery_target(
        project_id, "custom-render", "自定义规格更新", "LOCAL_FILESYSTEM",
        {"path_rel": "06_delivery/custom-render-v2", "width": 720, "height": 1280, "fps": 24},
    )

    assert second["id"] == first["id"]
    assert second["version_id"] != first["version_id"]
    assert second["version_no"] == 2
    snapshot = service.inspect_project_configuration(project_id)
    assert snapshot["selected_delivery_target_version_id"] == second["version_id"]
    versions = [item for item in snapshot["delivery_targets"] if item["target_id"] == first["id"]]
    assert {item["version_no"] for item in versions} == {1, 2}
    assert next(item for item in versions if item["version_no"] == 2)["spec"]["width"] == 720
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM delivery_targets WHERE project_id=? AND code=?", (project_id, "custom-render"),
        ).fetchone()[0] == 1

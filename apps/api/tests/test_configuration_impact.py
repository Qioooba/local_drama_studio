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


def test_selecting_one_target_version_resolves_project_wide_single_active(workspace, database) -> None:
    """Explicit target selection retires ACTIVE versions across ALL project
    targets so the configuration read model exposes a single selected id."""
    project = ProjectService(database, workspace.projects_root).create_project(
        code="target_selection_project", title="Target selection", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    service = ConfigurationService(database)
    service.create_delivery_target(project_id, "first-target", "First", "LOCAL_FILESYSTEM", {"path_rel": "06_delivery/first"})
    second = service.create_delivery_target(project_id, "second-target", "Second", "LOCAL_FILESYSTEM", {"path_rel": "06_delivery/second"})
    ambiguous = service.inspect_project_configuration(project_id)
    assert ambiguous["selected_delivery_target_version_id"] is None  # two ACTIVE versions
    selected = service.select_delivery_target_version(project_id, second["version_id"])
    assert selected["version_id"] == second["version_id"]
    resolved = service.inspect_project_configuration(project_id)
    assert resolved["selected_delivery_target_version_id"] == second["version_id"]
    with database.connect() as connection:
        active = connection.execute(
            "SELECT COUNT(*) FROM delivery_target_versions WHERE delivery_target_id IN "
            "(SELECT id FROM delivery_targets WHERE project_id=?) AND status='ACTIVE'",
            (project_id,),
        ).fetchone()[0]
    assert active == 1

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from local_drama.application.configuration import ConfigurationService
from local_drama.application.g7_readiness import G7ReadinessService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def _project(workspace, database, code: str) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title="G7 readiness",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )


def test_g7_readiness_is_read_only_and_reports_first_project_blocker(workspace, database) -> None:
    project = _project(workspace, database, "g7_readiness")
    ProfileService(database, workspace.manifest_path).sync_manifest()
    before = database.path.stat().st_size

    result = G7ReadinessService(database).inspect(str(project["id"]))

    assert result["status"] == "IN_PROGRESS"
    assert result["next_required_action"] == "PUBLISHED_CAPABILITY_PROFILE"
    assert result["runtime_contacted"] is False
    assert result["network_contacted"] is False
    assert result["mutated"] is False
    assert database.path.stat().st_size == before
    with TestClient(create_app(workspace)) as client:
        response = client.get(f"/api/v1/projects/{project['id']}/gates/g7")
    assert response.status_code == 200
    assert response.json()["readiness"]["next_required_action"] == "PUBLISHED_CAPABILITY_PROFILE"


def test_g7_readiness_keeps_later_wbs_blockers_after_local_bindings(workspace, database) -> None:
    project = _project(workspace, database, "g7_pass")
    project_id = str(project["id"])
    profiles = ProfileService(database, workspace.manifest_path)
    profiles.sync_manifest()
    profile = profiles.list_profiles()[0]
    with database.transaction() as connection:
        connection.execute("UPDATE execution_profile_versions SET status='PUBLISHED' WHERE id=?", (profile["version_id"],))

    configuration = ConfigurationService(database)
    configuration.create_plan_binding(project_id, "g7-plan", "G7 Plan", {"fps": "24/1"})
    configuration.create_delivery_target(
        project_id, "g7-local", "G7 Local", "LOCAL_FILESYSTEM", {"path_rel": "06_delivery/g7"}
    )
    configuration.bind_profile(project_id, str(profile["capability"]), str(profile["version_id"]))

    result = G7ReadinessService(database).inspect(project_id)
    assert result["status"] == "IN_PROGRESS"
    assert result["next_required_action"] == "PROFILE_EDITOR_TEST_PUBLISH"


def test_g7_readiness_rejects_non_loopback_runtime_and_unknown_project(workspace, database) -> None:
    project = _project(workspace, database, "g7_remote")
    ProfileService(database, workspace.manifest_path).sync_manifest()
    with database.transaction() as connection:
        connection.execute(
            "UPDATE local_runtimes SET base_url='https://example.invalid' WHERE transport='LOOPBACK_HTTP'"
        )
    result = G7ReadinessService(database).inspect(str(project["id"]))
    assert result["next_required_action"] == "NO_REMOTE_RUNTIME_TRANSPORT"

    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/projects/missing/gates/g7")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROJECT_NOT_FOUND"


def test_g7_readiness_accepts_compatibility_attestation_carried_into_published_version(workspace, database) -> None:
    project = _project(workspace, database, "g7_compatibility_attestation")
    project_id = str(project["id"])
    profiles = ProfileService(database, workspace.manifest_path)
    profiles.sync_manifest()
    source = profiles.list_profiles()[0]
    with database.transaction() as connection:
        connection.execute("UPDATE execution_profile_versions SET status='PUBLISHED' WHERE id=?", (source["version_id"],))
    draft = profiles.derive_contract_version(
        str(source["version_id"]),
        1,
        {"transport": "LOOPBACK_HTTP", "input_slots": {"FIRST_FRAME": {"min": 1, "max": 1}}},
        {"seed": {"determinism": "EXPLICIT"}, "capabilities": {
            "extend": {"support": "UNSUPPORTED", "required_inputs": []},
            "V2V": {"support": "UNSUPPORTED", "required_inputs": []},
            "reference": {"support": "UNSUPPORTED", "required_inputs": []},
            "motion": {"support": "NATIVE", "required_inputs": []},
            "camera": {"support": "UNSUPPORTED", "required_inputs": []},
        }},
        {"media_kind": "VIDEO", "container": "mp4", "codec": "h264"},
        {"gpu_heavy_concurrency": 1, "worker_policy": "ONE_H3_WORKER_ONE_GPU_TASK"},
    )
    validation = profiles.validate_contract_version(str(draft["id"]))
    compatibility = profiles.validate_compatibility(str(draft["id"]))
    capability = dict(draft["capability_contract"])
    capability.update({
        "contract_validation_attestation_id": validation["id"],
        "contract_compatibility_attestation_id": compatibility["id"],
    })
    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET status='PUBLISHED', capability_json=? WHERE id=?",
            (json.dumps(capability, separators=(",", ":")), draft["id"]),
        )
    configuration = ConfigurationService(database)
    configuration.create_plan_binding(project_id, "g7-compat-plan", "G7 compatibility plan", {"fps": "24/1"})
    configuration.create_delivery_target(project_id, "g7-compat-local", "G7 compat local", "LOCAL_FILESYSTEM", {"path_rel": "06_delivery/g7"})
    configuration.bind_profile(project_id, str(source["capability"]), str(draft["id"]))

    result = G7ReadinessService(database).inspect(project_id)
    compatibility_check = next(item for item in result["checks"] if item["code"] == "PROFILE_CAPABILITY_COMPATIBILITY")
    assert compatibility_check["passed"] is True
    assert result["next_required_action"] == "ZERO_PUBLIC_NETWORK_E2E"

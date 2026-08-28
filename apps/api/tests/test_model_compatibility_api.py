from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def test_model_compatibility_projection_api_is_read_only(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="model_snapshot_api",
        title="Model snapshot API",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    with TestClient(create_app(workspace)) as client:
        with database.connect() as connection:
            before = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("projects", "model_compatibility_reports", "audit_events", "outbox_events")}
        response = client.get(f"/api/v1/projects/{project['id']}/model-compatibility")
        with database.connect() as connection:
            after = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("projects", "model_compatibility_reports", "audit_events", "outbox_events")}
    assert response.status_code == 200
    payload = response.json()["compatibility"]
    assert payload["reports"]
    assert all(item["has_report"] is False for item in payload["reports"])
    assert payload["summary"] == {
        "artifact_count": len(payload["reports"]),
        "reported_count": 0,
        "pass_count": 0,
        "blocked_count": 0,
        "missing_license_evidence_count": len(payload["reports"]),
    }
    assert payload["runtime_contacted"] is False
    assert payload["network_contacted"] is False
    assert payload["mutated"] is False
    assert after == before


def test_model_compatibility_projection_rejects_unknown_project(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/projects/missing/model-compatibility")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROJECT_NOT_FOUND"


def test_global_model_registry_is_registered_once_and_available_without_project_context(workspace, database) -> None:
    path = workspace.work_root / "global-t2v.safetensors"
    header = b'{"x":{"dtype":"F16","shape":[1],"data_offsets":[0,0]}}'
    path.write_bytes(len(header).to_bytes(8, "little") + header + b"payload")

    with TestClient(create_app(workspace)) as client:
        registered = client.post(
            "/api/v1/model-registry/artifacts",
            json={"code": "global-t2v-test", "kind": "T2V", "machine_path_ref": str(path)},
        )
        assert registered.status_code == 201, registered.text
        artifact = registered.json()["artifact"]

        report = client.post(
            "/api/v1/model-registry/compatibility-report",
            json={"model_artifact_id": artifact["id"], "required_capability": "T2V"},
        )
        assert report.status_code == 201, report.text
        assert report.json()["report"]["report_status"] == "PASS"

        registry = client.get("/api/v1/model-registry")
        assert registry.status_code == 200, registry.text
        payload = registry.json()["compatibility"]

    item = next(entry for entry in payload["reports"] if entry["artifact_id"] == artifact["id"])
    assert item["report_status"] == "PASS"
    assert payload["scope"] == "GLOBAL"
    assert payload["available_to_all_projects"] is True
    assert payload["runtime_contacted"] is False
    assert payload["network_contacted"] is False
    assert payload["mutated"] is False

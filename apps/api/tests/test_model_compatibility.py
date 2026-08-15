from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.model_compatibility import ModelCompatibilityService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def test_registers_user_model_absolute_path_without_copying_or_uploading(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="user_model", title="User model", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    path = workspace.work_root / "user-model.safetensors"
    header = b'{"x":{"dtype":"F16","shape":[1],"data_offsets":[0,0]}}'
    path.write_bytes(len(header).to_bytes(8, "little") + header + b"payload")
    original_hash = path.read_bytes()
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v1/projects/{project['id']}/model-artifacts",
            json={"code": "user-model", "kind": "T2V", "machine_path_ref": str(path)},
        )
        assert response.status_code == 201, response.text
        artifact = response.json()["artifact"]
        assert artifact["copied"] is False and artifact["uploaded"] is False
        report = client.post(
            f"/api/v1/projects/{project['id']}/model-compatibility-report",
            json={"model_artifact_id": artifact["id"]},
        )
        assert report.status_code == 201
        assert report.json()["report"]["report_status"] == "PASS"
        assert report.json()["report"]["license_risk"] == "USER_RESPONSIBILITY_UNKNOWN"
    assert path.read_bytes() == original_hash


def test_model_report_hashes_user_supplied_safetensors_without_bundling_or_license_block(workspace, database) -> None:
    ProjectService(database, workspace.projects_root).create_project(
        code="model_report", title="Model report", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    path = workspace.work_root / "tiny.safetensors"
    header = b'{"x":{"dtype":"I8","shape":[1],"data_offsets":[0,0]}}'
    path.write_bytes(len(header).to_bytes(8, "little") + header + b"12345678")
    with database.transaction() as connection:
        artifact_id = "model-artifact-test"
        runtime_id = "runtime-test"
        connection.execute("INSERT INTO local_runtimes (id,code,title,transport,base_url,status,details_json,created_at,updated_at,created_by,revision,schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,1,'v2')", (runtime_id, "model-report-runtime", "model report", "LOCAL_PROCESS", None, "AVAILABLE", "{}", "now", "now", "test"))
        connection.execute("INSERT INTO model_artifacts (id,runtime_id,code,kind,machine_path_ref,license_note,compatibility_json,status,created_at,updated_at,created_by,revision,schema_version) VALUES (?,?,?,?,?,?,?,'CANDIDATE',?,?,?,?, 'v2')", (artifact_id, runtime_id, "model-report-artifact", "FL2VA DiT", str(path), "verify license", "{}", "now", "now", "test", 1))
    report = ModelCompatibilityService(database).report("model-artifact-test")
    assert len(report["sha256"]) == 64
    assert report["byte_size"] == path.stat().st_size
    assert report["report_status"] == "PASS"
    assert report["license_status"] == "UNVERIFIED_NO_LOCAL_LICENSE_EVIDENCE"
    assert report["license_risk"] == "USER_RESPONSIBILITY_UNKNOWN"
    assert report["distribution_scope"] == "REFERENCE_ONLY_NOT_BUNDLED"


def test_model_report_rejects_missing_artifact(workspace, database) -> None:
    from pytest import raises
    with raises(DomainRuleError) as raised:
        ModelCompatibilityService(database).report("missing")
    assert raised.value.code == "MODEL_ARTIFACT_NOT_FOUND"


def test_project_model_snapshot_is_read_only_and_exposes_non_blocking_license_risk(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="model_snapshot", title="Model snapshot", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    path = workspace.work_root / "snapshot.safetensors"
    header = b'{"x":{"dtype":"F16","shape":[1],"data_offsets":[0,0]}}'
    path.write_bytes(len(header).to_bytes(8, "little") + header + b"payload")
    with database.transaction() as connection:
        connection.execute("INSERT INTO local_runtimes (id,code,title,transport,base_url,status,details_json,created_at,updated_at,created_by,revision,schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,1,'v2')", ("runtime-snapshot", "snapshot-runtime", "snapshot", "LOCAL_PROCESS", None, "AVAILABLE", "{}", "now", "now", "test"))
        connection.execute("INSERT INTO model_artifacts (id,runtime_id,code,kind,machine_path_ref,license_note,compatibility_json,status,created_at,updated_at,created_by,revision,schema_version) VALUES (?,?,?,?,?,?,?,'CANDIDATE',?,?,?,?, 'v2')", ("model-snapshot", "runtime-snapshot", "model-snapshot", "H3 video VAE", str(path), "verify license", "{}", "now", "now", "test", 1))
    service = ModelCompatibilityService(database)
    service.report("model-snapshot", str(project["id"]))
    before = database.connect().execute("SELECT COUNT(*) FROM model_license_evidence").fetchone()[0]
    snapshot = service.project_snapshot(str(project["id"]))
    after = database.connect().execute("SELECT COUNT(*) FROM model_license_evidence").fetchone()[0]
    assert snapshot["summary"]["blocked_count"] == 0
    assert snapshot["summary"]["missing_license_evidence_count"] == 1
    assert snapshot["reports"][0]["blockers"] == []
    assert snapshot["runtime_contacted"] is False
    assert snapshot["network_contacted"] is False
    assert snapshot["mutated"] is False
    assert before == after

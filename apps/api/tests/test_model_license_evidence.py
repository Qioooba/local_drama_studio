from __future__ import annotations

import hashlib
import json

import pytest

from local_drama.application.model_compatibility import ModelCompatibilityService, _hash_and_header
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError


def _fixture(workspace, database):
    project = ProjectService(database, workspace.projects_root).create_project(
        code="license_evidence", title="License evidence", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    path = workspace.work_root / "tiny.safetensors"
    header = b'{"x":{"dtype":"F16","shape":[1],"data_offsets":[0,0]}}'
    path.write_bytes(len(header).to_bytes(8, "little") + header + b"12345678")
    with database.transaction() as connection:
        connection.execute("INSERT INTO local_runtimes (id,code,title,transport,base_url,status,details_json,created_at,updated_at,created_by,revision,schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,1,'v2')", ("runtime-license", "license-runtime", "license", "LOCAL_PROCESS", None, "AVAILABLE", "{}", "now", "now", "test"))
        connection.execute("INSERT INTO model_artifacts (id,runtime_id,code,kind,machine_path_ref,license_note,compatibility_json,status,created_at,updated_at,created_by,revision,schema_version) VALUES (?,?,?,?,?,?,?,'CANDIDATE',?,?,?,?, 'v2')", ("model-license", "runtime-license", "model-license", "H3 video VAE", str(path), "verify license", "{}", "now", "now", "test", 1))
    return project, path


def test_imported_local_license_evidence_unlocks_report(workspace, database) -> None:
    project, path = _fixture(workspace, database)
    artifact_sha, _, _ = _hash_and_header(path)
    project_root = workspace.projects_root / str(project["root_rel"])
    evidence = project_root / "00_admin" / "licenses" / "h3-video-vae.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps({"artifact_sha256": artifact_sha, "license_name": "Operator-owned local model record", "source": "operator-local-record"}), encoding="utf-8")
    service = ModelCompatibilityService(database)
    imported = service.import_license_evidence(str(project["id"]), "model-license", "00_admin/licenses/h3-video-vae.json", "Operator-owned local model record", "USER_OWNED", workspace)
    assert imported["license_status"] == "USER_OWNED"
    report = service.report("model-license", str(project["id"]))
    assert report["report_status"] == "PASS"
    assert report["license_evidence_id"] == imported["id"]


def test_license_evidence_must_match_current_model_hash(workspace, database) -> None:
    project, _ = _fixture(workspace, database)
    project_root = workspace.projects_root / str(project["root_rel"])
    evidence = project_root / "00_admin" / "licenses" / "bad.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps({"artifact_sha256": hashlib.sha256(b"wrong").hexdigest(), "license_name": "Wrong"}), encoding="utf-8")
    with pytest.raises(DomainRuleError) as raised:
        ModelCompatibilityService(database).import_license_evidence(str(project["id"]), "model-license", "00_admin/licenses/bad.json", "Wrong", "LOCAL_LICENSE_VERIFIED", workspace)
    assert raised.value.code == "MODEL_LICENSE_EVIDENCE_MISMATCH"


def test_license_evidence_path_cannot_escape_project(workspace, database) -> None:
    project, _ = _fixture(workspace, database)
    with pytest.raises(DomainRuleError) as raised:
        ModelCompatibilityService(database).import_license_evidence(str(project["id"]), "model-license", "../../outside.json", "x", "USER_OWNED", workspace)
    assert raised.value.code == "MODEL_LICENSE_EVIDENCE_PATH_INVALID"

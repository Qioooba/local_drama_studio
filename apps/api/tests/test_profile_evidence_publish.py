from __future__ import annotations

import json
import uuid

import pytest

from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError


def _create_evidence_job(database, jobs, project_id, profile_version_id, workflow_id, key, media_bindings=None):
    with database.connect() as connection:
        profile = connection.execute(
            "SELECT * FROM execution_profile_versions WHERE id=?", (profile_version_id,)
        ).fetchone()
    assert profile is not None
    return jobs.create_job(
        project_id, "PROFILE_EVIDENCE_PROBE", "EXECUTION_PROFILE_VERSION", profile_version_id, "CPU",
        {
            "workflow_version_id": workflow_id,
            "execution_snapshot": {
                "profile_version_id": profile_version_id,
                "profile_execution_fingerprint": ProfileService._execution_fingerprint(profile),
            },
            "media_bindings": media_bindings or [],
        },
        key, execution_profile_version_id=profile_version_id, max_attempts=1,
    )


def test_profile_publish_requires_complete_real_media_lineage(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="profile_evidence",
        title="Profile evidence",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60000,
        allow_unconfigured_capabilities=True,
    )
    profiles = ProfileService(database, workspace.manifest_path)
    candidate = next(item for item in profiles.sync_manifest()["profiles"] if item["capability"] == "VIDEO_T2V")
    workflow_id = str(uuid.uuid4())
    package = workspace.work_root / "workflow.json"
    package.write_text("{}", encoding="utf-8")
    now = "2026-08-13T00:00:00+00:00"
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO workflows (id, code, title, created_at, updated_at, created_by, revision, schema_version) VALUES (?, 'evidence_wf', 'Evidence', ?, ?, 'test', 1, 'v2')",
            (str(uuid.uuid4()), now, now),
        )
        workflow_parent = connection.execute("SELECT id FROM workflows WHERE code='evidence_wf'").fetchone()[0]
        connection.execute(
            """INSERT INTO workflow_versions
            (id, workflow_id, version_no, content_hash, package_rel_path, content_json, contract_json,
            node_bindings_json, runtime_contract_json, status, published_at, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, ?, 'workflow.json', ?, ?, '{}', '{}', 'PUBLISHED', ?, ?, ?, 'test', 1, 'v2')""",
            (
                workflow_id,
                workflow_parent,
                "0" * 64,
                json.dumps({
                    "1": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {}},
                    "2": {"class_type": "SaveVideo", "inputs": {}},
                }),
                json.dumps({"capability": "H3_T2VA_CANDIDATE", "input_slots": {}}),
                now,
                now,
                now,
            ),
        )
    jobs = JobService(database, workspace)
    job = _create_evidence_job(
        database, jobs, str(project["id"]), str(candidate["version_id"]), workflow_id, "profile-evidence-job",
    )
    claim = jobs.claim("profile-worker", ["CPU"])
    assert claim is not None and claim["job"]["id"] == job["id"]
    output = workspace.work_root / "profile-evidence.mp4"
    output.write_bytes(b"video")
    artifact = jobs.register_artifact(str(claim["attempt"]["id"]), "COMFY_OUTPUT", "profile-evidence.mp4")
    jobs.complete(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "profile-worker", success=True)
    media = MediaService(database, workspace).promote_job_artifact(str(artifact["id"]), media_kind="VIDEO")

    published = profiles.publish_from_evidence(str(candidate["version_id"]), str(media["media_version_id"]), workflow_id)
    replay = profiles.publish_from_evidence(str(candidate["version_id"]), str(media["media_version_id"]), workflow_id)
    assert published["status"] == "PUBLISHED"
    assert published["id"] == replay["id"]
    contract = json.loads(str(published["capability_json"]))
    assert contract["evidence_media_version_id"] == media["media_version_id"]
    assert contract["evidence_artifact_id"] == artifact["id"]
    assert contract["manifest_capability"] == {
        **contract["manifest_capability"],
        "status": "playable_success_verified",
        "playable_success_verified_in_this_run": True,
        "blocking_evidence": None,
        "node_family": "comfy_extras.MiniMaxH3ImageToVideo",
        "required_nodes": ["MiniMaxH3ImageToVideo", "SaveVideo"],
        "workflow": "workflow.json",
        "workflow_sha256": "0" * 64,
    }
    assert json.loads(str(published["input_contract_json"]))["input_slots"] == {}

    with database.transaction() as connection:
        connection.execute(
            "UPDATE workflow_versions SET contract_json=? WHERE id=?",
            (json.dumps({"capability": "H3_T2VA_CANDIDATE", "input_slots": {"FIRST_FRAME": {"min": 1, "max": 1}}}), workflow_id),
        )
    repaired = profiles.publish_from_evidence(str(candidate["version_id"]), str(media["media_version_id"]), workflow_id)
    assert repaired["id"] != published["id"]
    assert repaired["version_no"] == published["version_no"] + 1
    assert json.loads(str(repaired["input_contract_json"]))["input_slots"]["FIRST_FRAME"] == {"min": 1, "max": 1}


def test_evidence_publish_creates_new_version_when_draft_contract_differs_from_existing(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="evidence_contract_upgrade", title="Evidence contract upgrade", episode_count=1,
        aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    profiles = ProfileService(database, workspace.manifest_path)
    candidate = next(item for item in profiles.sync_manifest()["profiles"] if item["capability"] == "VIDEO_T2V")
    workflow_id = str(uuid.uuid4())
    package = workspace.work_root / "workflow.json"
    package.write_text("{}", encoding="utf-8")
    now = "2026-08-14T00:00:00+00:00"
    with database.transaction() as connection:
        parent_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO workflows (id, code, title, created_at, updated_at, created_by, revision, schema_version) VALUES (?, 'contract_upgrade_wf', 'Contract upgrade', ?, ?, 'test', 1, 'v2')",
            (parent_id, now, now),
        )
        connection.execute(
            """INSERT INTO workflow_versions
            (id, workflow_id, version_no, content_hash, package_rel_path, content_json, contract_json,
            node_bindings_json, runtime_contract_json, status, published_at, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, ?, 'workflow.json', '{}', ?, '{}', '{}', 'PUBLISHED', ?, ?, ?, 'test', 1, 'v2')""",
            (workflow_id, parent_id, "2" * 64, json.dumps({"capability": "H3_T2VA_CANDIDATE", "input_slots": {}}), now, now, now),
        )
    jobs = JobService(database, workspace)
    job = _create_evidence_job(
        database, jobs, str(project["id"]), str(candidate["version_id"]), workflow_id, "contract-upgrade-job",
    )
    claim = jobs.claim("contract-worker", ["CPU"])
    assert claim is not None and claim["job"]["id"] == job["id"]
    (workspace.work_root / "contract-upgrade.mp4").write_bytes(b"video")
    artifact = jobs.register_artifact(str(claim["attempt"]["id"]), "COMFY_OUTPUT", "contract-upgrade.mp4")
    jobs.complete(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "contract-worker", success=True)
    media = MediaService(database, workspace).promote_job_artifact(str(artifact["id"]), media_kind="VIDEO")

    baseline = profiles.publish_from_evidence(str(candidate["version_id"]), str(media["media_version_id"]), workflow_id)
    assert baseline["status"] == "PUBLISHED"
    assert json.loads(str(baseline["output_contract_json"])) == {}

    full: dict[str, dict[str, object]] = {
        "input_contract": {"transport": "LOOPBACK_HTTP", "input_slots": {}},
            "parameter_schema": {
                "seed": {"required": True, "determinism": "profile_declared"},
                "capabilities": {
                    "extend": {"support": "UNSUPPORTED", "required_inputs": []},
                    "V2V": {"support": "UNSUPPORTED", "required_inputs": []},
                    "reference": {"support": "UNSUPPORTED", "required_inputs": []},
                    "motion": {"support": "UNSUPPORTED", "required_inputs": []},
                },
            },
        "output_contract": {"media_kind": "VIDEO", "container": "mp4", "codec": "h264"},
        "resource_policy": {"gpu_heavy_concurrency": 1, "worker_policy": "ONE_H3_WORKER_ONE_GPU_TASK"},
    }
    draft = profiles.derive_contract_version(
        str(baseline["id"]), int(baseline["revision"]),
        full["input_contract"], full["parameter_schema"], full["output_contract"], full["resource_policy"],
    )
    validation = profiles.validate_contract_version(str(draft["id"]))
    assert validation["status"] == "PASS"
    compatibility = profiles.validate_compatibility(str(draft["id"]))
    assert compatibility["status"] == "PASS"
    draft_job = _create_evidence_job(
        database, jobs, str(project["id"]), str(draft["id"]), workflow_id, "contract-upgrade-draft-job",
    )
    draft_claim = jobs.claim("contract-draft-worker", ["CPU"])
    assert draft_claim is not None and draft_claim["job"]["id"] == draft_job["id"]
    (workspace.work_root / "contract-upgrade-draft.mp4").write_bytes(b"video")
    draft_artifact = jobs.register_artifact(
        str(draft_claim["attempt"]["id"]), "COMFY_OUTPUT", "contract-upgrade-draft.mp4",
    )
    jobs.complete(
        str(draft_claim["attempt"]["id"]), str(draft_claim["attempt"]["lease_token"]),
        "contract-draft-worker", success=True,
    )
    draft_media = MediaService(database, workspace).promote_job_artifact(
        str(draft_artifact["id"]), media_kind="VIDEO",
    )
    published = profiles.publish_from_evidence(str(draft["id"]), str(draft_media["media_version_id"]), workflow_id)
    assert published["id"] != baseline["id"]
    assert published["version_no"] > int(baseline["version_no"])
    assert json.loads(str(published["output_contract_json"])) == full["output_contract"]
    assert json.loads(str(published["resource_policy_json"])) == full["resource_policy"]
    contract = json.loads(str(published["capability_json"]))
    assert contract["contract_validation_attestation_id"] == validation["id"]
    replay = profiles.publish_from_evidence(str(draft["id"]), str(draft_media["media_version_id"]), workflow_id)
    assert replay["id"] == published["id"]


def test_evidence_publish_merges_profile_semantic_slots_with_workflow_slots(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="evidence_slot_merge", title="Evidence slot merge", episode_count=1,
        aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    profiles = ProfileService(database, workspace.manifest_path)
    candidate = next(item for item in profiles.sync_manifest()["profiles"] if item["capability"] == "VIDEO_T2V")
    workflow_id = str(uuid.uuid4())
    now = "2026-08-14T00:00:00+00:00"
    with database.transaction() as connection:
        parent_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO workflows (id, code, title, created_at, updated_at, created_by, revision, schema_version) VALUES (?, 'slot_merge_wf', 'Slot merge', ?, ?, 'test', 1, 'v2')",
            (parent_id, now, now),
        )
        connection.execute(
            """INSERT INTO workflow_versions
            (id, workflow_id, version_no, content_hash, package_rel_path, content_json, contract_json,
            node_bindings_json, runtime_contract_json, status, published_at, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, ?, 'workflow.json', '{}', ?, '{}', '{}', 'PUBLISHED', ?, ?, ?, 'test', 1, 'v2')""",
            (workflow_id, parent_id, "3" * 64, json.dumps({"capability": "H3_T2VA_CANDIDATE", "input_slots": {"FIRST_FRAME": {"min": 1, "max": 1}}}), now, now, now),
        )
    jobs = JobService(database, workspace)
    job = _create_evidence_job(
        database, jobs, str(project["id"]), str(candidate["version_id"]), workflow_id, "slot-merge-job",
    )
    claim = jobs.claim("slot-merge-worker", ["CPU"])
    assert claim is not None and claim["job"]["id"] == job["id"]
    (workspace.work_root / "slot-merge.mp4").write_bytes(b"video")
    artifact = jobs.register_artifact(str(claim["attempt"]["id"]), "COMFY_OUTPUT", "slot-merge.mp4")
    jobs.complete(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "slot-merge-worker", success=True)
    media = MediaService(database, workspace).promote_job_artifact(str(artifact["id"]), media_kind="VIDEO")
    baseline = profiles.publish_from_evidence(str(candidate["version_id"]), str(media["media_version_id"]), workflow_id)
    draft = profiles.derive_contract_version(
        str(baseline["id"]), int(baseline["revision"]),
        {"transport": "LOOPBACK_HTTP", "input_slots": {"MOTION_REFERENCE": {"min": 0, "max": 1}}},
        {"seed": {"determinism": "EXPLICIT"}, "capabilities": {}},
        {"media_kind": "VIDEO"}, {"gpu_heavy_concurrency": 1},
    )
    profiles.validate_contract_version(str(draft["id"]))
    with pytest.raises(DomainRuleError, match="capability compatibility"):
        profiles.publish_from_evidence(str(draft["id"]), str(media["media_version_id"]), workflow_id)


def test_i2v_publish_rejects_success_evidence_without_approved_first_frame(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="i2v_evidence_reject", title="I2V evidence reject", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    profiles = ProfileService(database, workspace.manifest_path)
    candidate = next(item for item in profiles.sync_manifest()["profiles"] if item["capability"] == "VIDEO_I2V")
    workflow_id = str(uuid.uuid4())
    now = "2026-08-13T00:00:00+00:00"
    with database.transaction() as connection:
        parent_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO workflows (id, code, title, created_at, updated_at, created_by, revision, schema_version) VALUES (?, 'i2v_evidence_wf', 'I2V evidence', ?, ?, 'test', 1, 'v2')",
            (parent_id, now, now),
        )
        connection.execute(
            """INSERT INTO workflow_versions
            (id, workflow_id, version_no, content_hash, package_rel_path, content_json, contract_json,
            node_bindings_json, runtime_contract_json, status, published_at, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, ?, 'workflow.json', '{}', ?, '{}', '{}', 'PUBLISHED', ?, ?, ?, 'test', 1, 'v2')""",
            (workflow_id, parent_id, "1" * 64, json.dumps({"capability": "H3_FL2VA_I2V_CANDIDATE"}), now, now, now),
        )
    jobs = JobService(database, workspace)
    job = _create_evidence_job(
        database, jobs, str(project["id"]), str(candidate["version_id"]), workflow_id,
        "i2v-evidence-no-frame", media_bindings=[],
    )
    claim = jobs.claim("i2v-profile-worker", ["CPU"])
    assert claim is not None and claim["job"]["id"] == job["id"]
    (workspace.work_root / "i2v-evidence.mp4").write_bytes(b"video")
    artifact = jobs.register_artifact(str(claim["attempt"]["id"]), "COMFY_OUTPUT", "i2v-evidence.mp4")
    jobs.complete(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "i2v-profile-worker", success=True)
    media = MediaService(database, workspace).promote_job_artifact(str(artifact["id"]), media_kind="VIDEO")

    with pytest.raises(DomainRuleError, match="FIRST_FRAME") as error:
        profiles.publish_from_evidence(str(candidate["version_id"]), str(media["media_version_id"]), workflow_id)
    assert error.value.code == "PROFILE_EVIDENCE_APPROVED_FIRST_FRAME_REQUIRED"

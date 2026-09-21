from __future__ import annotations

import json
import uuid

from local_drama.application.i2v_probe import I2VProbePlanService
from local_drama.application.media import MediaService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082")


def test_prepare_i2v_probe_keyframe_copies_reviews_selects_and_reuses(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="i2v_probe_bootstrap",
        title="I2V probe bootstrap",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    source_path = workspace.work_root / "profile-evidence.png"
    source_path.write_bytes(PNG)
    source = MediaService(database, workspace).import_file(
        str(project["id"]),
        source_path,
        purpose="PROFILE_EVIDENCE",
        owner_type="EXECUTION_PROFILE_VERSION",
        owner_id=str(uuid.uuid4()),
        media_kind="IMAGE",
        stage="KEYFRAME",
    )

    service = I2VProbePlanService(database, workspace)
    prepared = service.prepare_keyframe(
        str(project["id"]),
        str(source["media_version_id"]),
        True,
    )["approved_keyframe"]
    assert prepared["reused"] is False
    assert prepared["source_media_version_id"] == source["media_version_id"]
    assert prepared["media_version_id"] != source["media_version_id"]

    keyframe = MediaService(database, workspace).get_version(str(prepared["media_version_id"]))
    assert keyframe["owner_type"] == "PROJECT"
    assert keyframe["owner_id"] == project["id"]
    assert prepared["owner_id"] == project["id"]
    assert keyframe["purpose"] == "PROFILE_EVIDENCE_KEYFRAME"
    assert keyframe["stage"] == "KEYFRAME"
    assert keyframe["approved_version_id"] == prepared["media_version_id"]
    assert keyframe["selected_version_id"] == prepared["media_version_id"]
    assert keyframe["sha256"] == MediaService(database, workspace).get_version(
        str(source["media_version_id"])
    )["sha256"]
    with database.connect() as connection:
        assert ProfileService._approved_i2v_first_frame(
            connection,
            str(prepared["media_version_id"]),
            str(project["id"]),
        ) is not None

    reused = service.prepare_keyframe(
        str(project["id"]),
        str(source["media_version_id"]),
        True,
    )["approved_keyframe"]
    assert reused["reused"] is True
    assert reused["media_version_id"] == prepared["media_version_id"]
    plan = service.plan(str(project["id"]))
    assert "APPROVED_KEYFRAME_REQUIRED" not in plan["blockers"]
    assert plan["snapshot"]["approved_keyframe"]["media_version_id"] == prepared["media_version_id"]


def test_i2v_probe_plan_is_read_only_and_freezes_approved_evidence(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="i2v_probe_plan", title="I2V probe plan", episode_count=1, aspect_ratio="9:16",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    source = workspace.work_root / "probe.png"
    source.write_bytes(PNG)
    media = MediaService(database, workspace).import_file(
        str(project["id"]), source, purpose="SOURCE_IMAGE", owner_type="PROJECT", owner_id=str(project["id"]),
        media_kind="IMAGE", stage="KEYFRAME",
    )
    profile_sync = ProfileService(database, workspace.manifest_path).sync_manifest()
    workflow_id = str(uuid.uuid4())
    now = "2026-08-13T00:00:00Z"
    with database.transaction() as connection:
        parent = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO workflows (id, code, title, created_at, updated_at, created_by, revision, schema_version) VALUES (?, 'probe_i2v', 'Probe I2V', ?, ?, 'test', 1, 'v2')",
            (parent, now, now),
        )
        connection.execute(
            """INSERT INTO workflow_versions
            (id, workflow_id, version_no, content_hash, content_json, contract_json, node_bindings_json,
            runtime_contract_json, status, published_at, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, ?, '{}', ?, '{}', '{}', 'PUBLISHED', ?, ?, ?, 'test', 1, 'v2')""",
            (workflow_id, parent, "a" * 64, json.dumps({"capability": "H3_FL2VA_I2V_CANDIDATE"}), now, now, now),
        )
    blocked = I2VProbePlanService(database).plan(str(project["id"]))
    assert blocked["status"] == "BLOCKED"
    assert blocked["would_create_job"] is False
    prepared = I2VProbePlanService(database, workspace).prepare_keyframe(
        str(project["id"]), str(media["media_version_id"]), True,
    )["approved_keyframe"]
    ready = I2VProbePlanService(database).plan(str(project["id"]))
    assert ready["status"] == "READY"
    assert ready["snapshot"]["approved_keyframe"]["approval_id"] == prepared["approval_id"]
    assert ready["snapshot"]["workflow"]["id"] == workflow_id
    assert ready["would_create_job"] is False
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0

    candidate_profile_id = next(
        str(item["version_id"]) for item in profile_sync["profiles"] if item["capability"] == "VIDEO_I2V"
    )
    explicit_workflow_id = str(uuid.uuid4())
    with database.transaction() as connection:
        explicit_parent = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO workflows (id, code, title, created_at, updated_at, created_by, revision, schema_version) VALUES (?, 'probe_i2v_fast', 'Probe I2V FAST', ?, ?, 'test', 1, 'v2')",
            (explicit_parent, now, now),
        )
        connection.execute(
            """INSERT INTO workflow_versions
            (id, workflow_id, version_no, content_hash, content_json, contract_json, node_bindings_json,
            runtime_contract_json, status, published_at, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, ?, '{}', ?, ?, '{}', 'PUBLISHED', ?, ?, ?, 'test', 1, 'v2')""",
            (
                explicit_workflow_id,
                explicit_parent,
                "b" * 64,
                json.dumps({
                    "capability": "H3_FL2VA_I2V_CANDIDATE",
                    "production_tier": "FAST",
                    "authoring_parameters": {
                        "aspect_ratio": "16:9",
                        "acceleration": "OFF",
                        "native_audio": False,
                    },
                }),
                json.dumps({role: {} for role in (
                    "PROMPT", "SEED", "FIRST_FRAME", "ASPECT_RATIO", "NATIVE_AUDIO", "OUTPUT_PREFIX",
                )}),
                now,
                now,
                now,
            ),
        )
        connection.execute(
            "UPDATE execution_profile_versions SET status='DRAFT', workflow_version_id=? WHERE id=?",
            (workflow_id, candidate_profile_id),
        )
    explicit = I2VProbePlanService(database).plan(
        str(project["id"]), candidate_profile_id, explicit_workflow_id
    )
    assert explicit["snapshot"]["workflow"]["id"] == explicit_workflow_id
    assert explicit["snapshot"]["workflow_selection"] == "EXPLICIT"
    assert explicit["snapshot"]["semantic_inputs"]["ASPECT_RATIO"] == "16:9"
    assert explicit["snapshot"]["semantic_inputs"]["NATIVE_AUDIO"] is False
    assert explicit["plan_hash"] != ready["plan_hash"]

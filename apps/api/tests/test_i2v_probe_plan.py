from __future__ import annotations

import json
import uuid

from local_drama.application.i2v_probe import I2VProbePlanService
from local_drama.application.media import MediaService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082")


def test_i2v_probe_plan_is_read_only_and_freezes_approved_evidence(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="i2v_probe_plan", title="I2V probe plan", episode_count=1, aspect_ratio="9:16",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    source = workspace.work_root / "probe.png"
    source.write_bytes(PNG)
    media = MediaService(database, workspace).import_file(
        str(project["id"]), source, purpose="KEYFRAME", owner_type="SHOT", owner_id=str(shot["id"]),
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
    reviews = ReviewService(database, workspace)
    reviews.ensure_templates()
    template = next(item for item in reviews.templates() if item["code"] == "image_asset")
    approval = reviews.submit_review(
        str(media["media_version_id"]), str(template["id"]), "APPROVED", 1,
        [{"item_id": str(item["id"]), "result": "PASS"} for item in template["items"]],
    )
    ready = I2VProbePlanService(database).plan(str(project["id"]))
    assert ready["status"] == "READY"
    assert ready["snapshot"]["approved_keyframe"]["approval_id"] == approval["id"]
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
                json.dumps({"capability": "H3_FL2VA_I2V_CANDIDATE", "production_tier": "FAST"}),
                json.dumps({role: {} for role in ("PROMPT", "SEED", "FIRST_FRAME", "OUTPUT_PREFIX")}),
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
    assert explicit["plan_hash"] != ready["plan_hash"]

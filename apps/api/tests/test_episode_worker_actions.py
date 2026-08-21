from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from local_drama.application.episode_worker_actions import EpisodeWorkerActionService
from local_drama.application.generation import GenerationService
from local_drama.application.jobs import JobService
from local_drama.application.projects import ProjectService


def _project_and_shot(workspace, database, code: str) -> tuple[dict, dict, dict]:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code, title=code, episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "SH-001", 4_000)
    return project, episode, shot


def test_video_action_retries_only_the_failed_shot_job(workspace, database) -> None:
    project, episode, shot = _project_and_shot(workspace, database, "episode_video_retry")
    intent = GenerationService(database, workspace).create_intent(
        str(project["id"]), "SHOT", str(shot["id"]), "I2V_FORMAL", "retry failed shot",
    )
    profile_id = str(uuid.uuid4())
    variant_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        connection.execute("INSERT INTO execution_profiles (id,code,title) VALUES ('ep-action-profile','ep-action-profile','test')")
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,
             parameter_schema_json,status,capability_json,output_contract_json,resource_policy_json)
            VALUES (?,'ep-action-profile',1,'VIDEO_I2V','{}','{}','{}','PUBLISHED','{}','{}','{}')""",
            (profile_id,),
        )
        connection.execute(
            """INSERT INTO generation_variants
            (id,intent_id,variant_no,variant_type,parent_variant_id,branch_reason,prompt_revision_id,
             capability_profile_version_id,parameter_set_json,seed_policy,explicit_seed,input_fingerprint,
             recipe_hash,status,created_at,updated_at,created_by)
            VALUES (?,?,1,'BASE',NULL,'TEST',NULL,?,'{}','EXPLICIT',7,?,?,'QUEUED',?,?,'test')""",
            (variant_id, intent["id"], profile_id, "0" * 64, "1" * 64, now, now),
        )
    jobs = JobService(database, workspace)
    failed = jobs.create_job(
        str(project["id"]), "GENERATION_VARIANT", "GENERATION_VARIANT", variant_id, "GPU_H3", {}, "failed-shot-job", max_attempts=1,
    )
    claim = jobs.claim("gpu-test", ["GPU_H3"])
    assert claim is not None
    jobs.complete(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "gpu-test", success=False, error_code="RUNTIME_FAILED")

    report, produced_bytes = EpisodeWorkerActionService(database, workspace).video_generation(
        str(episode["id"]), "run-1", "task-1",
    )

    assert produced_bytes == 0
    assert report["status"] == "PASS"
    item = report["produced"]["items"][0]
    assert item["status"] == "RETRIED"
    assert item["retry_of_job_id"] == failed["id"]
    assert item["variant_id"] == variant_id
    assert jobs.get_job(str(item["job_id"]))["state"] == "QUEUED"


def test_qc_action_persists_machine_evidence_without_human_approval(workspace, database) -> None:
    project, episode, shot = _project_and_shot(workspace, database, "episode_qc_evidence")
    root = workspace.projects_root / str(project["root_rel"])
    media_path = root / "04_media" / "shot-proxy.mp4"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"locally-probed-video")
    digest = hashlib.sha256(media_path.read_bytes()).hexdigest()
    asset_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO media_assets
            (id,project_id,owner_type,owner_id,purpose,media_kind,selected_version_id,version_counter,
             metadata_json,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?, 'SHOT',?,'SHOT_VIDEO_CANDIDATE','VIDEO',?,1,'{}',?,?,'test',1,'v2')""",
            (asset_id, project["id"], shot["id"], version_id, now, now),
        )
        connection.execute(
            """INSERT INTO media_versions
            (id,media_asset_id,version_no,take_no,stage,rel_path,mime_type,byte_size,sha256,probe_json,
             integrity_status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,1,1,'PROXY',?,'video/mp4',?,?,?,'VERIFIED',?,?,'test',1,'v2')""",
            (version_id, asset_id, media_path.relative_to(root).as_posix(), media_path.stat().st_size, digest, json.dumps({"probe_status": "PASS"}), now, now),
        )

    report, _ = EpisodeWorkerActionService(database, workspace).qc(str(episode["id"]), "run-2", "task-2")

    assert report["status"] == "PASS"
    assert report["machine_check"]["evidence_type"] == "MACHINE_QC_ONLY"
    assert report["machine_check"]["human_approval_status"] == "PENDING"
    assert report["machine_check"]["human_approval_created"] is False
    with database.connect() as connection:
        checks = connection.execute("SELECT status FROM machine_check_runs WHERE subject_id=?", (version_id,)).fetchall()
        approvals = connection.execute("SELECT id FROM review_decisions WHERE subject_id=?", (version_id,)).fetchall()
    assert [row["status"] for row in checks] == ["PASS"]
    assert approvals == []


def test_qc_reroll_limit_resolver_defaults_to_one_and_is_bounded(workspace, database) -> None:
    service = EpisodeWorkerActionService(database, workspace)
    assert service._qc_reroll_limit("p", "e", "s") == 1
    disabled = EpisodeWorkerActionService(database, workspace, qc_reroll_limit_resolver=lambda *_: 0)
    assert disabled._qc_reroll_limit("p", "e", "s") == 0
    capped = EpisodeWorkerActionService(database, workspace, qc_reroll_limit_resolver=lambda *_: 500)
    assert capped._qc_reroll_limit("p", "e", "s") == 10

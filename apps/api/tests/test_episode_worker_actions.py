from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from local_drama.application.episode_worker_actions import EpisodeWorkerActionService
from local_drama.application.generation import GenerationService
from local_drama.application.jobs import JobService
from local_drama.application.projects import ProjectService
from tests.test_director_fields import _published_camera_profile


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


def test_keyframe_action_dispatches_shared_batch_and_preserves_job_dependencies(workspace, database, monkeypatch):
    from unittest.mock import Mock
    from local_drama.application.worker_handlers.automation_task import advance_automation_run

    _, episode, shot = _project_and_shot(workspace, database, "keyframe_dispatch")
    service = EpisodeWorkerActionService(database, workspace)
    service.keyframe_batches = Mock()
    service.keyframe_batches.plan.return_value = {"valid": True, "issues": [], "summary": {"blocked": 0}, "plan_hash": "hash"}
    service.keyframe_batches.submit.return_value = {"id": "batch", "items": [{"shot_id": shot["id"], "status": "QUEUED", "job_id": "image-job"}]}
    report, size = service.keyframe_generation(str(episode["id"]), "run", "task")
    assert report["status"] == "PASS"
    args = service.keyframe_batches.submit.call_args.kwargs
    assert args["idempotency_key"] == "episode-keyframes:run:task"
    assert args["targets"] == [{"shot_id": shot["id"], "expected_revision": shot["revision"]}]
    assert args["candidate_count"] == 1
    stepper = Mock()
    advance_automation_run({"id": "task-job", "input_snapshot": {"automation_run_id": "run"}}, report, size, workflow_steps=stepper)
    assert stepper.step_run.call_args.kwargs["additional_dependency_job_ids"] == ["image-job"]
    # Retrying the same task must use the batch service's stable replay key.
    service.keyframe_generation(str(episode["id"]), "run", "task")
    assert service.keyframe_batches.submit.call_args.kwargs["idempotency_key"] == args["idempotency_key"]


def test_keyframe_action_reuses_approval_and_blocks_partial_plan(workspace, database, monkeypatch):
    from unittest.mock import Mock
    import local_drama.application.episode_worker_actions as module

    _, episode, shot = _project_and_shot(workspace, database, "keyframe_block")
    service = EpisodeWorkerActionService(database, workspace)
    service.keyframe_batches = Mock()
    monkeypatch.setattr(module, "approved_keyframes_for_shots", lambda *args, **kwargs: {str(shot["id"]): {"media_version_id": "approved"}})
    report, _ = service.keyframe_generation(str(episode["id"]), "run", "task")
    assert report["status"] == "PASS"
    service.keyframe_batches.plan.assert_not_called()
    monkeypatch.setattr(module, "approved_keyframes_for_shots", lambda *args, **kwargs: {})
    service.keyframe_batches.plan.return_value = {"valid": True, "issues": [{"code": "CONFIG_MISSING"}], "summary": {"blocked": 1}}
    report, _ = service.keyframe_generation(str(episode["id"]), "run", "task")
    assert report["status"] == "NEEDS_HITL"
    service.keyframe_batches.submit.assert_not_called()


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


def test_video_action_refreshes_stale_working_media_instead_of_reusing_verified_history(
    workspace, database, monkeypatch,
) -> None:
    service = EpisodeWorkerActionService(database, workspace)
    shot = {"id": "shot-stale", "code": "SHOT-STALE"}
    submitted: list[str] = []
    monkeypatch.setattr(service, "_episode", lambda _episode_id: ("project-stale", [shot]))
    monkeypatch.setattr(service, "_stale_working_media_shots", lambda _episode_id, _shot_ids: {"shot-stale"})
    monkeypatch.setattr(service, "_variant_jobs", lambda _shot_id: [{"id": "old-job", "state": "SUCCEEDED"}])
    monkeypatch.setattr(service, "_promote_completed_outputs", lambda _jobs: [])
    monkeypatch.setattr(service, "_shot_video", lambda _shot_id: {"media_version_id": "verified-old-video"})
    monkeypatch.setattr(service, "_shot_video_count", lambda _shot_id: 1)

    def submit(_project_id, current_shot, _run_id, _task_id, **_kwargs):
        submitted.append(str(current_shot["id"]))
        return {
            "shot_id": str(current_shot["id"]),
            "shot_code": str(current_shot["code"]),
            "status": "SUBMITTED",
            "variant_id": "fresh-variant",
            "job_id": "fresh-job",
        }

    monkeypatch.setattr(service, "_submit_shot", submit)

    report, produced_bytes = service.video_generation("episode-stale", "run-stale", "task-stale")

    assert produced_bytes == 0
    assert report["status"] == "PASS"
    assert submitted == ["shot-stale"]
    item = report["produced"]["items"][0]
    assert item["status"] == "SUBMITTED"
    assert item["forced_new_take"] is True
    assert item["stale_working_media"] is True
    assert item["refresh_reason"] == "WORKING_MEDIA_DEPENDENCY_CHANGED"


def test_shot_video_queries_ignore_stale_variants_without_cross_project_candidate_leakage(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project_a = projects.create_project(
        code="stale_query_a", title="stale-query-a", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    project_b = projects.create_project(
        code="stale_query_b", title="stale-query-b", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    episode_a = projects.list_episodes(str(projects.list_seasons(str(project_a["id"]))[0]["id"]))[0]
    episode_b = projects.list_episodes(str(projects.list_seasons(str(project_b["id"]))[0]["id"]))[0]
    shot_a = projects.create_shot(str(episode_a["id"]), "SH-A", 4_000)
    shot_b = projects.create_shot(str(episode_b["id"]), "SH-B", 4_000)
    generation = GenerationService(database, workspace)
    intent_a = generation.create_intent(str(project_a["id"]), "SHOT", str(shot_a["id"]), "I2V_FORMAL", "stale query A")
    intent_b = generation.create_intent(str(project_b["id"]), "SHOT", str(shot_b["id"]), "I2V_FORMAL", "stale query B")
    profile_id = _published_camera_profile(workspace, database, "NATIVE")
    now = datetime.now(UTC).isoformat()
    variants = [(str(uuid.uuid4()), intent_a["id"], 1, 1), (str(uuid.uuid4()), intent_b["id"], 1, 0)]
    with database.transaction() as connection:
        for variant_id, intent_id, variant_no, is_stale in variants:
            connection.execute(
                """INSERT INTO generation_variants
                (id,intent_id,variant_no,variant_type,parent_variant_id,branch_reason,prompt_revision_id,
                 capability_profile_version_id,parameter_set_json,seed_policy,explicit_seed,input_fingerprint,
                 recipe_hash,status,is_stale,stale_reason,created_at,updated_at,created_by)
                VALUES (?,?,?,'BASE',NULL,'stale query',NULL,?,'{}','EXPLICIT',7,?,?,'QUEUED',?,?,?,?,?)""",
                (variant_id, intent_id, variant_no, profile_id, "0" * 64, "1" * 64, is_stale, "dependency changed" if is_stale else None, now, now, "test"),
            )
            asset_id = str(uuid.uuid4())
            media_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO media_assets
                (id,project_id,owner_type,owner_id,purpose,media_kind,selected_version_id,version_counter,
                 metadata_json,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?, 'GENERATION_VARIANT',?,'SHOT_VIDEO_CANDIDATE','VIDEO',?,1,'{}',?,?,'test',1,'v2')""",
                (asset_id, str(project_a["id"] if intent_id == intent_a["id"] else project_b["id"]), variant_id, media_id, now, now),
            )
            connection.execute(
                """INSERT INTO media_versions
                (id,media_asset_id,version_no,take_no,stage,rel_path,mime_type,byte_size,sha256,probe_json,
                 integrity_status,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,1,1,'FORMAL','candidate.mp4','video/mp4',1,?,'{}','VERIFIED',?,?,'test',1,'v2')""",
                (media_id, asset_id, str(variant_no) * 64, now, now),
            )

    service = EpisodeWorkerActionService(database, workspace)
    assert service._shot_video_count(str(shot_a["id"])) == 0
    assert service._shot_video_count(str(shot_b["id"])) == 1


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

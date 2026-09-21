from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

import pytest

from local_drama.application.episode_worker_actions import EpisodeWorkerActionService
from local_drama.application.generation import GenerationService
from local_drama.application.jobs import JobService
from local_drama.application.production_choices import ProductionChoiceService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from tests.test_director_fields import _published_camera_profile


def _project_and_shot(workspace, database, code: str) -> tuple[dict, dict, dict]:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
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
    report, size = service.keyframe_generation(str(episode["id"]), "run", "task", dispatch_job_limit=2)
    assert report["status"] == "PASS"
    args = service.keyframe_batches.submit.call_args.kwargs
    assert args["idempotency_key"] == "episode-keyframes:run:task"
    assert args["targets"] == [{"shot_id": shot["id"], "expected_revision": shot["revision"]}]
    assert args["candidate_count"] == 1
    assert args["max_jobs"] == 2
    stepper = Mock()
    advance_automation_run({"id": "task-job", "input_snapshot": {"automation_run_id": "run"}}, report, size, workflow_steps=stepper)
    assert stepper.step_run.call_args.kwargs["additional_dependency_job_ids"] == ["image-job"]
    # Retrying the same task must use the batch service's stable replay key.
    service.keyframe_generation(str(episode["id"]), "run", "task", dispatch_job_limit=2)
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


def test_keyframe_action_reuses_same_session_temporary_choice(workspace, database, monkeypatch):
    from unittest.mock import Mock

    import local_drama.application.episode_worker_actions as module

    _, episode, shot = _project_and_shot(workspace, database, "keyframe_session_reuse")
    service = EpisodeWorkerActionService(database, workspace)
    service.keyframe_batches = Mock()
    monkeypatch.setattr(module, "approved_keyframes_for_shots", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        module,
        "session_keyframes_for_shots",
        lambda _connection, session_id, _shot_ids: {
            str(shot["id"]): {
                "media_version_id": "session-keyframe",
                "production_choice_id": "choice-1",
                "selection_authority": "MACHINE_TEMPORARY",
                "human_approved": False,
            }
        }
        if session_id == "session-1"
        else {},
    )

    report, produced_bytes = service.keyframe_generation(
        str(episode["id"]),
        "run",
        "task",
        production_session_id="session-1",
    )

    assert produced_bytes == 0
    assert report["status"] == "PASS"
    assert report["machine_check"]["code"] == "KEYFRAME_INPUTS_REUSED"
    assert report["produced"]["items"] == [
        {
            "shot_id": str(shot["id"]),
            "status": "SESSION_TEMPORARY_REUSED",
            "media_version_id": "session-keyframe",
            "production_choice_id": "choice-1",
            "selection_authority": "MACHINE_TEMPORARY",
            "human_approved": False,
        }
    ]
    service.keyframe_batches.plan.assert_not_called()
    service.keyframe_batches.submit.assert_not_called()


def test_video_action_retries_only_the_failed_shot_job(workspace, database) -> None:
    project, episode, shot = _project_and_shot(workspace, database, "episode_video_retry")
    intent = GenerationService(database, workspace).create_intent(
        str(project["id"]),
        "SHOT",
        str(shot["id"]),
        "I2V_FORMAL",
        "retry failed shot",
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
        str(project["id"]),
        "GENERATION_VARIANT",
        "GENERATION_VARIANT",
        variant_id,
        "GPU_H3",
        {},
        "failed-shot-job",
        max_attempts=1,
    )
    claim = jobs.claim("gpu-test", ["GPU_H3"])
    assert claim is not None
    jobs.complete(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "gpu-test", success=False, error_code="RUNTIME_FAILED")

    report, produced_bytes = EpisodeWorkerActionService(database, workspace).video_generation(
        str(episode["id"]),
        "run-1",
        "task-1",
    )

    assert produced_bytes == 0
    assert report["status"] == "PASS"
    item = report["produced"]["items"][0]
    assert item["status"] == "RETRIED"
    assert item["retry_of_job_id"] == failed["id"]
    assert item["variant_id"] == variant_id
    assert jobs.get_job(str(item["job_id"]))["state"] == "QUEUED"


def test_video_action_refreshes_stale_working_media_instead_of_reusing_verified_history(
    workspace,
    database,
    monkeypatch,
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


def test_video_action_dispatches_only_one_bounded_wave(workspace, database, monkeypatch) -> None:
    service = EpisodeWorkerActionService(database, workspace)
    shots = [{"id": f"shot-{index}", "code": f"SH-{index:03d}"} for index in range(1, 4)]
    submitted: list[str] = []
    monkeypatch.setattr(service, "_episode", lambda _episode_id: ("project-wave", shots))
    monkeypatch.setattr(service, "_stale_working_media_shots", lambda *_args: set())
    monkeypatch.setattr(service, "_variant_jobs", lambda _shot_id: [])
    monkeypatch.setattr(service, "_promote_completed_outputs", lambda _jobs: [])
    monkeypatch.setattr(service, "_shot_video", lambda _shot_id: None)
    monkeypatch.setattr(service, "_shot_video_count", lambda _shot_id: 0)

    def submit(_project_id, shot, _run_id, _task_id, **kwargs):
        job_id = f"job-{len(submitted) + 1}"
        submitted.append(job_id)
        return {
            "shot_id": shot["id"],
            "shot_code": shot["code"],
            "status": "SUBMITTED",
            "variant_id": f"variant-{len(submitted)}",
            "job_id": job_id,
            "take_index": kwargs["take_index"],
        }

    monkeypatch.setattr(service, "_submit_shot", submit)
    report, _ = service.video_generation(
        "episode-wave",
        "run-wave",
        "task-wave",
        target_take_count=2,
        dispatch_job_limit=3,
    )

    assert report["status"] == "PASS"
    assert submitted == ["job-1", "job-2", "job-3"]
    assert report["machine_check"]["dispatch_job_limit"] == 3
    assert report["machine_check"]["deferred_shots"] == 1


def test_session_video_submission_freezes_session_authority_in_variant_plan(
    workspace,
    database,
    monkeypatch,
) -> None:
    project, _episode, shot = _project_and_shot(workspace, database, "session_video_authority")
    service = EpisodeWorkerActionService(database, workspace)
    captured_plans = []
    monkeypatch.setattr(
        service,
        "_keyframe",
        lambda _shot_id, *, production_session_id=None: {
            "media_version_id": "session-keyframe",
            "selection_authority": "MACHINE_TEMPORARY",
            "production_choice_id": "choice-1",
        }
        if production_session_id == "session-1"
        else None,
    )
    monkeypatch.setattr(
        service,
        "_video_profile",
        lambda _project_id, _shot_id: {
            "id": "video-profile",
            "input_contract_json": json.dumps({"input_slots": {"FIRST_FRAME": {"max": 1}}}),
        },
    )
    monkeypatch.setattr(service, "_fields", lambda _shot: {"target_duration_ms": 4_000})
    monkeypatch.setattr(service, "_end_frame_chain", lambda *_args: {"status": "SKIPPED"})
    monkeypatch.setattr(service.generation, "_retarget_camera_plan", lambda *_args: None)

    def preflight(_intent_id, plan):
        captured_plans.append(plan)
        return {"plan_hash": "plan-hash"}

    def submit(_intent_id, plan, _plan_hash, _idempotency_key):
        captured_plans.append(plan)
        return {"variant": {"id": "variant-1"}, "job": {"id": "job-1"}}

    monkeypatch.setattr(service.generation, "preflight_variant", preflight)
    monkeypatch.setattr(service.generation, "submit_confirmed_variant", submit)

    result = service._submit_shot(
        str(project["id"]),
        {**shot, "code": "SH-001"},
        "run-1",
        "task-1",
        production_session_id="session-1",
    )

    assert result["status"] == "SUBMITTED"
    assert len(captured_plans) == 2
    assert all(plan.production_session_id == "session-1" for plan in captured_plans)
    assert result["keyframe_selection_authority"] == "MACHINE_TEMPORARY"
    assert result["production_choice_id"] == "choice-1"


def test_operation_impact_distinguishes_retry_new_take_and_recompose_without_writes(
    workspace,
    database,
    monkeypatch,
) -> None:
    project, episode, persisted_shot = _project_and_shot(workspace, database, "operation_impact")
    service = EpisodeWorkerActionService(database, workspace)
    shot = {"id": str(persisted_shot["id"]), "code": "SH-001", "revision": 3}
    monkeypatch.setattr(service, "_episode", lambda _episode_id: (str(project["id"]), [shot]))
    monkeypatch.setattr(
        service,
        "video_generation_preflight",
        lambda *_args, **_kwargs: {
            "input_fingerprint": "e" * 64,
            "items": [
                {
                    "shot_id": shot["id"],
                    "status": "READY",
                    "blockers": [],
                    "profile_version_id": "profile-operation",
                }
            ],
        },
    )
    monkeypatch.setattr(service, "_stale_working_media_shots", lambda *_args: set())
    monkeypatch.setattr(
        service,
        "_variant_jobs",
        lambda _shot_id: [
            {
                "id": "failed-job",
                "state": "FAILED",
                "variant_id": "variant-1",
                "explicit_seed": 77,
            }
        ],
    )
    monkeypatch.setattr(service, "_shot_video_count", lambda _shot_id: 0)

    retry = service.operation_impact(str(episode["id"]), operation="RETRY_ORIGINAL", target_shot_ids=(shot["id"],))
    assert retry["sets"]["retry_original"] == [
        {
            "shot_id": shot["id"],
            "shot_code": "SH-001",
            "shot_revision": 3,
            "reason": "FAILED_JOB_SAME_FROZEN_INPUTS",
            "job_id": "failed-job",
            "variant_id": "variant-1",
            "seed": 77,
        }
    ]
    assert retry["mutated"] is False
    assert retry["expected_input_fingerprints"] == {}

    new_take = service.operation_impact(str(episode["id"]), operation="NEW_TAKE", target_shot_ids=(shot["id"],))
    assert new_take["sets"]["needs_generation"][0]["reason"] == "EXPLICIT_NEW_CANDIDATE"
    assert new_take["gpu_video_job_count"] == 1
    assert new_take["plan_hash"] != retry["plan_hash"]

    recompose = service.operation_impact(str(episode["id"]), operation="RECOMPOSE_ONLY")
    assert recompose["gpu_video_job_count"] == 0
    assert recompose["sets"]["blocked_by_dependency"][0]["reason"] == "TIMELINE_REQUIRED"


def test_video_generation_rejects_inputs_changed_after_operation_preview(
    workspace,
    database,
    monkeypatch,
) -> None:
    service = EpisodeWorkerActionService(database, workspace)
    shot = {"id": "shot-frozen", "code": "SH-001"}
    monkeypatch.setattr(service, "_episode", lambda _episode_id: ("project-1", [shot]))
    monkeypatch.setattr(
        service,
        "video_generation_preflight",
        lambda *_args, **_kwargs: {
            "items": [{"shot_id": shot["id"], "status": "READY", "prompt": "changed"}],
        },
    )

    with pytest.raises(DomainRuleError) as stale:
        service.video_generation(
            "episode-1",
            "run-1",
            "task-1",
            target_shot_ids=(shot["id"],),
            expected_input_fingerprints={shot["id"]: "f" * 64},
        )
    assert stale.value.code == "EPISODE_OPERATION_PLAN_STALE"


def test_retry_original_requeues_the_failed_job_without_resolving_current_generation_inputs(
    workspace,
    database,
    monkeypatch,
) -> None:
    service = EpisodeWorkerActionService(database, workspace)
    shot = {"id": "shot-frozen-retry", "code": "SH-001", "revision": 2}
    failed = {"id": "job-failed", "state": "FAILED", "variant_id": "variant-frozen"}
    monkeypatch.setattr(service, "_episode", lambda _episode_id: ("project-1", [shot]))
    monkeypatch.setattr(service, "_variant_jobs", lambda _shot_id: [failed])
    monkeypatch.setattr(service, "_stale_working_media_shots", lambda *_args: {shot["id"]})
    monkeypatch.setattr(service, "_promote_completed_outputs", lambda *_args: [])
    monkeypatch.setattr(service, "_shot_video", lambda *_args: None)
    monkeypatch.setattr(service, "_shot_video_count", lambda *_args: 0)
    monkeypatch.setattr(service, "_submit_shot", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not submit a new take")))
    retried: list[str] = []
    monkeypatch.setattr(
        service.jobs,
        "retry",
        lambda job_id, actor="local-user": retried.append(job_id) or {"id": job_id},
    )

    report, _ = service.video_generation(
        "episode-1",
        "run-1",
        "task-1",
        target_shot_ids=(shot["id"],),
        retry_original_only=True,
    )

    assert retried == ["job-failed"]
    assert report["produced"]["items"][0]["frozen_input_reused"] is True
    assert report["machine_check"]["retry_original_only"] is True


def test_qc_pass_with_adoption_blocker_pauses_production(workspace, database, monkeypatch) -> None:
    service = EpisodeWorkerActionService(database, workspace)
    shot = {"id": "shot-adoption-block", "code": "S001"}
    monkeypatch.setattr(service, "_episode", lambda _episode_id: ("project-1", [shot]))
    monkeypatch.setattr(service, "_variant_jobs", lambda _shot_id: [])
    monkeypatch.setattr(
        service,
        "_shot_video",
        lambda _shot_id: {
            "media_version_id": "video-pass",
            "media_asset_id": "asset-pass",
            "stage": "PROXY",
            "selected_version_id": None,
            "approved_version_id": None,
            "variant_id": None,
        },
    )
    monkeypatch.setattr(service.reviews, "machine_check", lambda *_args, **_kwargs: {"id": "qc-1", "status": "PASS"})
    monkeypatch.setattr(service, "_auto_select_video", lambda *_args: {"status": "BLOCKED", "code": "SELECTION_CONFLICT"})

    report, _ = service.qc("episode-1", "run-1", "task-1", auto_select=True)

    assert report["status"] == "NEEDS_HITL"
    item = report["produced"]["items"][0]
    assert item["status"] == "PASS"
    assert item["production_status"] == "BLOCKED"
    assert item["auto_selection"] == {"status": "BLOCKED", "code": "SELECTION_CONFLICT"}


def test_session_qc_records_temporary_choice_without_global_auto_selection(
    workspace, database, monkeypatch
) -> None:
    service = EpisodeWorkerActionService(database, workspace)
    shot = {"id": "shot-session-choice", "code": "S001"}
    monkeypatch.setattr(service, "_episode", lambda _episode_id: ("project-1", [shot]))
    monkeypatch.setattr(service, "_variant_jobs", lambda _shot_id: [])
    monkeypatch.setattr(
        service,
        "_shot_video",
        lambda _shot_id: {
            "media_version_id": "video-pass",
            "media_asset_id": "asset-pass",
            "stage": "FORMAL",
            "selected_version_id": None,
            "approved_version_id": None,
            "variant_id": None,
        },
    )
    monkeypatch.setattr(
        service.reviews,
        "machine_check",
        lambda *_args, **_kwargs: {"id": "qc-session", "status": "PASS"},
    )
    monkeypatch.setattr(
        service,
        "_auto_select_video",
        lambda *_args: (_ for _ in ()).throw(AssertionError("session QC must not write global selection")),
    )
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ProductionChoiceService,
        "record_video_choice",
        lambda _self, session_id, _episode_id, _shot_id, media_version_id, _check_id, **_kwargs: (
            recorded.append((session_id, media_version_id))
            or {"selection_authority": "MACHINE_TEMPORARY", "human_approved": False}
        ),
    )

    report, _ = service.qc(
        "episode-1",
        "run-1",
        "task-1",
        auto_select=True,
        production_session_id="session-1",
    )

    assert report["status"] == "PASS"
    assert recorded == [("session-1", "video-pass")]
    item = report["produced"]["items"][0]
    assert "auto_selection" not in item
    assert item["production_choice"]["selection_authority"] == "MACHINE_TEMPORARY"


def test_older_qc_pass_candidate_beats_newer_failed_candidate(workspace, database) -> None:
    project, _episode_row, shot = _project_and_shot(workspace, database, "qc_candidate_order")
    now_a = "2026-09-13T00:00:00+00:00"
    now_b = "2026-09-13T00:00:01+00:00"
    candidate_ids: list[str] = []
    with database.transaction() as connection:
        for ordinal, (created_at, status) in enumerate(((now_a, "PASS"), (now_b, "FAIL")), start=1):
            asset_id = str(uuid.uuid4())
            media_id = str(uuid.uuid4())
            candidate_ids.append(media_id)
            connection.execute(
                """INSERT INTO media_assets
                (id,project_id,owner_type,owner_id,purpose,media_kind,version_counter,metadata_json,
                 created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,'SHOT',?,'SHOT_VIDEO_CANDIDATE','VIDEO',1,'{}',?,?,'test',1,'v2')""",
                (asset_id, project["id"], shot["id"], created_at, created_at),
            )
            connection.execute(
                """INSERT INTO media_versions
                (id,media_asset_id,version_no,take_no,stage,rel_path,mime_type,byte_size,sha256,probe_json,
                 integrity_status,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,1,?,'PROXY',?,'video/mp4',1,?,'{}','VERIFIED',?,?,'test',1,'v2')""",
                (media_id, asset_id, ordinal, f"candidate-{ordinal}.mp4", str(ordinal) * 64, created_at, created_at),
            )
            connection.execute(
                """INSERT INTO machine_check_runs
                (id,subject_type,subject_id,policy_version,status,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,'MEDIA_VERSION',?,'g4_media_qc_v1',?,?,?,'test',1,'v2')""",
                (str(uuid.uuid4()), media_id, status, created_at, created_at),
            )

    selected = EpisodeWorkerActionService(database, workspace)._shot_video(str(shot["id"]))
    assert selected is not None
    assert selected["media_version_id"] == candidate_ids[0]
    assert selected["machine_check_status"] == "PASS"


def test_shot_video_queries_ignore_stale_variants_without_cross_project_candidate_leakage(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project_a = projects.create_project(
        code="stale_query_a",
        title="stale-query-a",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    project_b = projects.create_project(
        code="stale_query_b",
        title="stale-query-b",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
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

from __future__ import annotations

import json

import pytest

from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.commands.generation_preferences import GenerationPreferenceCommandService
from local_drama.application.episode_production_runs import EpisodeProductionRunService
from local_drama.application.episode_worker_actions import EpisodeWorkerActionService
from local_drama.application.jobs import JobService
from local_drama.application.projects import ProjectService
from local_drama.application.worker_handlers.automation_task import advance_automation_run
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.generation_preference_repository import (
    SqliteGenerationPreferenceRepository,
)


def _episode(workspace, database, code: str) -> tuple[dict, dict]:
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
    return project, projects.list_episodes(str(season["id"]))[0]


def test_episode_preflight_rejects_unknown_production_mode(workspace, database) -> None:
    _project, episode = _episode(workspace, database, "episode_mode_invalid")
    service = EpisodeProductionRunService(database, workspace)

    with pytest.raises(DomainRuleError) as error:
        service.preflight(str(episode["id"]), production_mode="turbo", min_free_disk_bytes=1)

    assert error.value.code == "EPISODE_PRODUCTION_MODE_INVALID"


def test_cancelled_run_never_labels_incomplete_stage_as_running() -> None:
    state = EpisodeProductionRunService._state

    assert state(0, 9, 0, 0, 0, "CANCELLED") == "CANCELLED"
    assert state(1, 9, 0, 0, 0, "CANCELLED") == "CANCELLED"
    assert state(9, 9, 0, 0, 0, "CANCELLED") == "COMPLETED"


def test_video_action_resolves_shot_preference_instead_of_legacy_project_binding(workspace, database) -> None:
    project, episode = _episode(workspace, database, "episode_profile_resolution")
    shot = ProjectService(database, workspace.projects_root).create_shot(str(episode["id"]), "SHOT-001", 4_000)
    with database.transaction() as connection:
        for profile_id, version_id, code, timestamp in (
            ("profile-legacy", "version-legacy", "legacy-i2v", "2026-08-20T00:00:00Z"),
            ("profile-current", "version-current", "current-i2v", "2026-08-21T00:00:00Z"),
        ):
            connection.execute(
                """INSERT INTO execution_profiles
                (id,code,title,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,'test',1,'v2')""",
                (profile_id, code, code, timestamp, timestamp),
            )
            connection.execute(
                """INSERT INTO execution_profile_versions
                (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,
                 parameter_schema_json,status,created_at,updated_at,created_by,revision,schema_version,
                 capability_json,output_contract_json,resource_policy_json)
                VALUES (?,?,1,'VIDEO_I2V','{}','{}','{}','PUBLISHED',?,?,'test',1,'v2','{}','{}','{}')""",
                (version_id, profile_id, timestamp, timestamp),
            )
        connection.execute(
            """INSERT INTO project_profile_bindings
            (id,project_id,capability,execution_profile_version_id,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES ('legacy-binding',?,'VIDEO_I2V','version-legacy','ACTIVE','now','now','test',1,'v2')""",
            (str(project["id"]),),
        )
        GenerationPreferenceCommandService(SqliteGenerationPreferenceRepository(connection)).put(
            project_id=str(project["id"]),
            owner_type="SHOT",
            owner_id=str(shot["id"]),
            capability="VIDEO_I2V",
            resolution_mode="EXPLICIT",
            execution_profile_version_id="version-current",
            reason="the Director explicitly selected the current profile",
        )

    resolved = EpisodeWorkerActionService(database, workspace)._video_profile(
        str(project["id"]), str(shot["id"]),
    )

    assert resolved["id"] == "version-current"


def test_episode_preflight_uses_effective_project_auto_video_profile(
    workspace, database,
) -> None:
    project, episode = _episode(workspace, database, "episode_profile_auto_preflight")
    project_id = str(project["id"])
    profile_id = "version-auto-preflight"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO execution_profiles
            (id,code,title,created_at,updated_at,created_by,revision,schema_version)
            VALUES ('profile-auto-preflight','auto-preflight','Auto preflight',
                    '2026-08-21T00:00:00Z','2026-08-21T00:00:00Z','test',1,'v2')"""
        )
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,
             parameter_schema_json,status,created_at,updated_at,created_by,revision,schema_version,
             capability_json,output_contract_json,resource_policy_json,workflow_version_id)
            VALUES (?,'profile-auto-preflight',1,'VIDEO_I2V','{}','{}','{}','PUBLISHED',
                    '2026-08-21T00:00:00Z','2026-08-21T00:00:00Z','test',1,'v2','{}','{}','{}',
                    'workflow-auto-preflight')""",
            (profile_id,),
        )
        GenerationPreferenceCommandService(SqliteGenerationPreferenceRepository(connection)).put(
            project_id=project_id,
            owner_type="PROJECT",
            owner_id=project_id,
            capability="VIDEO_I2V",
            resolution_mode="AUTO",
            reason="use the project's automatic video profile",
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM project_profile_bindings WHERE project_id=?", (project_id,),
        ).fetchone()[0] == 0

    preflight = EpisodeProductionRunService(database, workspace).preflight(
        str(episode["id"]), tts_enabled=False, min_free_disk_bytes=1,
    )
    profile_check = next(item for item in preflight["checks"] if item["code"] == "PROFILE_CAPABILITY_MISSING")
    assert profile_check["status"] == "PASS"
    assert profile_check["evidence"]["effective_profile_version_id"] == profile_id
    assert profile_check["evidence"]["resolution_source"] == "EFFECTIVE_VIDEO_PROFILE"


def test_episode_preflight_keeps_effective_video_profile_project_scoped(
    workspace, database,
) -> None:
    project_a, _episode_a = _episode(workspace, database, "episode_profile_scope_a")
    project_b, episode_b = _episode(workspace, database, "episode_profile_scope_b")
    profiles = (
        (str(project_a["id"]), "profile-scope-a", "version-scope-a"),
        (str(project_b["id"]), "profile-scope-b", "version-scope-b"),
    )
    with database.transaction() as connection:
        for project_id, profile_id, version_id in profiles:
            connection.execute(
                """INSERT INTO execution_profiles
                (id,code,title,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?, '2026-08-21T00:00:00Z','2026-08-21T00:00:00Z','test',1,'v2')""",
                (profile_id, profile_id, profile_id),
            )
            connection.execute(
                """INSERT INTO execution_profile_versions
                (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,
                 parameter_schema_json,status,created_at,updated_at,created_by,revision,schema_version,
                 capability_json,output_contract_json,resource_policy_json,workflow_version_id)
                VALUES (?,?,1,'VIDEO_I2V','{}','{}','{}','PUBLISHED',
                        '2026-08-21T00:00:00Z','2026-08-21T00:00:00Z','test',1,'v2','{}','{}','{}',?)""",
                (version_id, profile_id, f"workflow-{version_id}"),
            )
            connection.execute(
                """INSERT INTO project_profile_bindings
                (id,project_id,capability,execution_profile_version_id,status,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?, 'VIDEO_I2V',?,'ACTIVE','2026-08-21T00:00:00Z','2026-08-21T00:00:00Z','test',1,'v2')""",
                (f"binding-{project_id}", project_id, version_id),
            )

    preflight = EpisodeProductionRunService(database, workspace).preflight(
        str(episode_b["id"]), tts_enabled=False, min_free_disk_bytes=1,
    )
    profile_check = next(item for item in preflight["checks"] if item["code"] == "PROFILE_CAPABILITY_MISSING")
    assert profile_check["status"] == "PASS"
    assert profile_check["evidence"]["effective_profile_version_id"] == "version-scope-b"


def test_episode_preflight_uses_live_comfy_probe_over_stale_registered_status(
    workspace, database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project, episode = _episode(workspace, database, "episode_comfy_message")
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO local_runtimes
            (id,code,title,transport,base_url,status,details_json,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,1,'v2')""",
            ("runtime-comfy-blocked", "comfy-uat", "Comfy UAT", "LOOPBACK_HTTP", "http://127.0.0.1:8188", "BLOCKED", "{}", "now", "now", "test"),
        )
    monkeypatch.setattr(
        "local_drama.application.episode_production_runs._probe_loopback",
        lambda _url: ("PASS", {"status_code": 200, "loopback": True}),
    )

    preflight = EpisodeProductionRunService(database, workspace).preflight(
        str(episode["id"]), tts_enabled=False, min_free_disk_bytes=1,
    )
    check = next(item for item in preflight["checks"] if item["code"] == "COMFY_ADAPTER_UNAVAILABLE")
    assert check["blocking"] is False
    assert check["detail"] == "Comfy adapter 已声明，loopback 实时探测可用（登记状态 BLOCKED）"
    assert check["evidence"]["registered_runtime_status"] == "BLOCKED"
    assert check["evidence"]["probe_status"] == "PASS"


def test_mode_is_fingerprinted_and_frozen_in_workflow_snapshot(workspace, database) -> None:
    project, episode = _episode(workspace, database, "episode_mode_snapshot")
    service = EpisodeProductionRunService(database, workspace)
    draft = service.preflight(str(episode["id"]), tts_enabled=False, production_mode="draft", min_free_disk_bytes=1)
    quality = service.preflight(str(episode["id"]), tts_enabled=False, production_mode="QUALITY", min_free_disk_bytes=1)

    assert draft["production_mode"] == "DRAFT"
    assert draft["mode_policy"]["target_take_count"] == 1
    assert quality["mode_policy"]["target_take_count"] == 4
    assert draft["input_fingerprint"] != quality["input_fingerprint"]

    episode_context = {**episode, "project_id": project["id"]}
    draft_workflow = service._workflow_for_snapshot(episode_context, draft, actor="test")
    quality_workflow = service._workflow_for_snapshot(episode_context, quality, actor="test")
    assert draft_workflow["id"] != quality_workflow["id"]
    for workflow, mode, take_count in ((draft_workflow, "DRAFT", 1), (quality_workflow, "QUALITY", 4)):
        metadata = workflow["definition"]["nodes"][0]["metadata"]
        assert metadata["production_mode"] == mode
        assert metadata["mode_policy"]["target_take_count"] == take_count
        assert all(item["payload"]["production_mode"] == mode for item in workflow["definition"]["batch_items"])
        assert all(item["payload"]["mode_policy"]["target_take_count"] == take_count for item in workflow["definition"]["batch_items"])

    # Creating another mode-specific workflow never mutates the prior frozen definition.
    reread_draft = service.automation.get_workflow(str(draft_workflow["id"]))
    assert reread_draft["definition"]["nodes"][0]["metadata"]["mode_policy"]["target_take_count"] == 1


@pytest.mark.parametrize(("mode", "target_take_count"), (("DRAFT", 1), ("BALANCED", 2), ("QUALITY", 4)))
def test_video_action_submits_mode_target_candidate_count(
    workspace, database, monkeypatch: pytest.MonkeyPatch, mode: str, target_take_count: int,
) -> None:
    service = EpisodeWorkerActionService(database, workspace)
    shot = {"id": "shot-mode", "code": "SHOT-MODE"}
    submitted_take_indexes: list[int] = []
    monkeypatch.setattr(service, "_episode", lambda _episode_id: ("project-mode", [shot]))
    monkeypatch.setattr(service, "_variant_jobs", lambda _shot_id: [])
    monkeypatch.setattr(service, "_promote_completed_outputs", lambda _jobs: [])
    monkeypatch.setattr(service, "_shot_video", lambda _shot_id: None)
    monkeypatch.setattr(service, "_shot_video_count", lambda _shot_id: 0)

    def submit(_project_id, _shot, _run_id, _task_id, *, take_index=0, previous_shot=None):
        submitted_take_indexes.append(take_index)
        return {
            "shot_id": "shot-mode",
            "shot_code": "SHOT-MODE",
            "status": "SUBMITTED",
            "variant_id": f"variant-{take_index}",
            "job_id": f"job-{take_index}",
            "take_index": take_index,
        }

    monkeypatch.setattr(service, "_submit_shot", submit)
    report, produced_bytes = service.video_generation(
        "episode-mode", "run-mode", "task-mode", target_take_count=target_take_count,
    )

    assert produced_bytes == 0
    assert report["status"] == "PASS"
    assert report["machine_check"]["target_take_count"] == target_take_count
    assert submitted_take_indexes == list(range(target_take_count)), mode


def test_video_action_force_new_take_filters_selected_shots_and_ignores_existing_candidate(
    workspace, database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = EpisodeWorkerActionService(database, workspace)
    shots = [
        {"id": "shot-a", "code": "SHOT-A"},
        {"id": "shot-b", "code": "SHOT-B"},
        {"id": "shot-c", "code": "SHOT-C"},
    ]
    submitted: list[tuple[str, str | None]] = []
    monkeypatch.setattr(service, "_episode", lambda _episode_id: ("project-mode", shots))
    monkeypatch.setattr(service, "_variant_jobs", lambda _shot_id: [{"id": "old-job", "state": "SUCCEEDED"}])
    monkeypatch.setattr(service, "_promote_completed_outputs", lambda _jobs: ["old-media"])
    monkeypatch.setattr(service, "_shot_video", lambda _shot_id: {"media_version_id": "existing-video"})
    monkeypatch.setattr(service, "_shot_video_count", lambda _shot_id: 1)

    def submit(_project_id, shot, _run_id, _task_id, *, take_index=0, previous_shot=None):
        submitted.append((str(shot["id"]), str(previous_shot["id"]) if previous_shot else None))
        return {"shot_id": shot["id"], "shot_code": shot["code"], "status": "SUBMITTED", "job_id": "job-new", "variant_id": "variant-new"}

    monkeypatch.setattr(service, "_submit_shot", submit)
    report, _ = service.video_generation(
        "episode-mode",
        "run-mode",
        "task-mode",
        target_shot_ids=("shot-b",),
        force_new_take=True,
    )

    assert submitted == [("shot-b", "shot-a")]
    assert report["produced"]["items"][0]["forced_new_take"] is True
    assert report["machine_check"]["target_shot_ids"] == ["shot-b"]


def test_selected_shot_video_preflight_compiles_persisted_modifiers_without_runtime_contact(
    workspace, database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = EpisodeWorkerActionService(database, workspace)
    fields = {
        "schema_version": "director-intent.v3",
        "shot_type": "MEDIUM",
        "composition": {"preset": "RULE_OF_THIRDS", "framing": "FULL", "subject_position": "CENTER", "depth_plan": "MID"},
        "subject_action": "主角推门进入",
        "performance": {"emotion": "警惕"},
        "camera_plan": {"mode": "NATIVE", "shot_type": "MEDIUM", "movement": "DOLLY_IN", "direction": "FORWARD", "intensity": 0.5, "curve": "LINEAR", "profile_version_id": "profile-ready"},
        "target_duration_ms": 4_000,
        "dialogue": "有人吗？",
        "environment": "废弃车站",
        "continuity": "承接前镜",
        "creative_intent": "压迫感",
        "prompt_modifiers": ["雨夜", "冷色调"],
    }
    shot = {
        "id": "shot-ready", "code": "SHOT-READY", "revision": 7,
        "current_revision_id": "revision-7", "fields_json": json.dumps(fields, ensure_ascii=False),
    }
    monkeypatch.setattr(service, "_episode", lambda _episode_id: ("project-ready", [shot]))
    monkeypatch.setattr(service, "_approved_keyframe", lambda _shot_id: {"media_version_id": "keyframe-ready"})
    monkeypatch.setattr(
        service,
        "_video_profile",
        lambda _project_id, _shot_id: {"id": "profile-ready", "input_contract_json": '{"input_slots":{"FIRST_FRAME":{"max":1}}}'},
    )

    preflight = service.video_generation_preflight("episode-ready", target_shot_ids=("shot-ready",))

    assert preflight["status"] == "READY"
    assert preflight["mutated"] is False
    assert preflight["runtime_contacted"] is False
    assert preflight["items"][0]["prompt"].endswith("统一视觉修饰：雨夜，冷色调")


def test_recover_uses_original_quality_mode_snapshot(workspace, database) -> None:
    project, episode = _episode(workspace, database, "episode_mode_recover")
    service = EpisodeProductionRunService(database, workspace)
    preflight = service.preflight(
        str(episode["id"]), tts_enabled=False, production_mode="QUALITY", min_free_disk_bytes=1_000_000,
    )
    workflow = service._workflow_for_snapshot({**episode, "project_id": project["id"]}, preflight, actor="test")
    run = service.automation.start_run(
        str(workflow["id"]), plan_hash=str(workflow["plan_hash"]), idempotency_key="quality-recovery",
    )

    recovered = service.recover(str(run["id"]))

    assert recovered["run"]["production_mode"] == "QUALITY"
    assert recovered["run"]["mode_policy"]["target_take_count"] == 4
    refreshed_workflow = service.automation.get_workflow(str(workflow["id"]))
    assert refreshed_workflow["definition"]["nodes"][0]["metadata"]["production_mode"] == "QUALITY"
    assert refreshed_workflow["definition"]["batch_items"][0]["payload"]["mode_policy"]["target_take_count"] == 4


def test_quality_retry_still_fills_all_remaining_candidate_slots(
    workspace, database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = EpisodeWorkerActionService(database, workspace)
    shot = {"id": "shot-quality-retry", "code": "SHOT-QUALITY"}
    failed = {"id": "failed-job", "variant_id": "failed-variant", "state": "FAILED"}
    submitted_take_indexes: list[int] = []
    monkeypatch.setattr(service, "_episode", lambda _episode_id: ("project-quality", [shot]))
    monkeypatch.setattr(service, "_variant_jobs", lambda _shot_id: [failed])
    monkeypatch.setattr(service, "_promote_completed_outputs", lambda _jobs: [])
    monkeypatch.setattr(service, "_shot_video", lambda _shot_id: None)
    monkeypatch.setattr(service, "_shot_video_count", lambda _shot_id: 0)
    monkeypatch.setattr(service.jobs, "retry", lambda _job_id, actor: {"id": "failed-job"})

    def submit(_project_id, _shot, _run_id, _task_id, *, take_index=0, previous_shot=None):
        submitted_take_indexes.append(take_index)
        return {
            "shot_id": str(_shot["id"]),
            "shot_code": str(_shot["code"]),
            "status": "SUBMITTED",
            "variant_id": f"variant-{take_index}",
            "job_id": f"job-{take_index}",
            "take_index": take_index,
        }

    monkeypatch.setattr(service, "_submit_shot", submit)
    report, _ = service.video_generation("episode-quality", "run-quality", "task-quality", target_take_count=4)

    item = report["produced"]["items"][0]
    assert report["status"] == "PASS"
    assert item["target_take_count"] == 4
    assert [candidate["job_id"] for candidate in item["submissions"]] == [
        "failed-job",
        "job-1",
        "job-2",
        "job-3",
    ]
    assert submitted_take_indexes == [1, 2, 3]


def test_qc_automation_task_waits_for_every_video_child_job(workspace, database) -> None:
    project, episode = _episode(workspace, database, "episode_mode_dependencies")
    automation = AutomationWorkflowService(database)
    workflow = automation.create_workflow(
        str(project["id"]),
        code="mode-dependency-test",
        title="Mode dependency test",
        mode="BATCH_AUTOMATED",
        nodes=[{"id": "episode", "type": "EPISODE_PRODUCTION_TASK"}],
        batch_items=[
            {"key": "VIDEO", "payload": {"action": "VIDEO_GENERATION", "episode_id": episode["id"]}},
            {"key": "QC", "payload": {"action": "QC", "episode_id": episode["id"]}},
        ],
        conditions=[],
        max_iterations=3,
        max_tasks=3,
        max_disk_bytes=1_000_000,
        human_gate="NONE",
    )
    run = automation.start_run(
        str(workflow["id"]), plan_hash=str(workflow["plan_hash"]), idempotency_key="mode-dependency-run",
    )
    jobs = JobService(database, workspace)
    video_job_id = str(run["tasks"][0]["job_id"])
    video_claim = jobs.claim("automation-video", ["CPU"])
    assert video_claim is not None and video_claim["job"]["id"] == video_job_id
    jobs.complete(
        str(video_claim["attempt"]["id"]), str(video_claim["attempt"]["lease_token"]), "automation-video", success=True,
    )
    children = [
        jobs.create_job(
            str(project["id"]), "CPU_TEST", "GENERATION_VARIANT", f"variant-{index}", "GPU_H3", {},
            f"mode-child-{index}",
        )
        for index in range(2)
    ]
    report = {
        "machine_check": {"status": "PASS", "ok": True},
        "produced": {"items": [{"status": "SUBMITTED", "submissions": [{"job_id": child["id"]} for child in children]}]},
    }
    assert advance_automation_run(
        jobs.get_job(video_job_id), report, 0,
        workflow_steps=AutomationWorkflowService(database),
    ) is None

    advanced = automation.get_run(str(run["id"]))
    qc_job_id = str(advanced["tasks"][1]["job_id"])
    with database.connect() as connection:
        dependency_ids = {
            str(row["depends_on_job_id"])
            for row in connection.execute("SELECT depends_on_job_id FROM job_dependencies WHERE job_id=?", (qc_job_id,))
        }
    assert dependency_ids == {video_job_id, *(str(child["id"]) for child in children)}
    assert jobs.claim("qc-must-wait", ["CPU"]) is None

    for index in range(2):
        claim = jobs.claim(f"gpu-{index}", ["GPU_H3"])
        assert claim is not None
        jobs.complete(
            str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), f"gpu-{index}", success=True,
        )
    qc_claim = jobs.claim("qc-after-children", ["CPU"])
    assert qc_claim is not None and qc_claim["job"]["id"] == qc_job_id


def test_qc_promotes_successful_video_artifacts_before_candidate_lookup(
    workspace, database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = EpisodeWorkerActionService(database, workspace)
    shot = {"id": "shot-qc-promote", "code": "SHOT-QC"}
    promoted = False
    monkeypatch.setattr(service, "_episode", lambda _episode_id: ("project-qc", [shot]))
    monkeypatch.setattr(service, "_variant_jobs", lambda _shot_id: [{"id": "gpu-job", "state": "SUCCEEDED"}])

    def promote(jobs):
        nonlocal promoted
        assert [item["id"] for item in jobs] == ["gpu-job"]
        promoted = True
        return ["video-version"]

    def video(_shot_id):
        assert promoted is True
        return {"media_version_id": "video-version", "variant_id": None}

    monkeypatch.setattr(service, "_promote_completed_outputs", promote)
    monkeypatch.setattr(service, "_shot_video", video)
    monkeypatch.setattr(service.reviews, "machine_check", lambda _media_version_id, actor: {"id": "check", "status": "PASS"})

    report, _ = service.qc("episode-qc", "run-qc", "task-qc")

    assert promoted is True
    assert report["status"] == "PASS"
    assert report["produced"]["items"][0]["media_version_id"] == "video-version"


def test_episode_worker_reads_canonical_media_promotion_response_shape(workspace, database) -> None:
    service = EpisodeWorkerActionService(database, workspace)

    assert service._promoted_version_id({"id": "media-version-1", "mime_type": "video/mp4"}) == "media-version-1"
    assert service._promoted_version_id({"media_version_id": "media-version-legacy"}) == "media-version-legacy"
    with pytest.raises(DomainRuleError) as raised:
        service._promoted_version_id({"mime_type": "video/mp4"})
    assert raised.value.code == "MEDIA_PROMOTION_RESPONSE_INVALID"

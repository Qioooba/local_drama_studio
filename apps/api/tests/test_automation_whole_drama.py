"""G11 P0-4 whole-drama one-click orchestration: WHOLE_DRAMA template + executor.

The declarative engine (automation_workflows.py) is covered by
test_automation_workflows.py.  This file covers the built-in template expansion
and the LocalMediaWorker AUTOMATION_WORKFLOW_TASK executor contract: report
artifact, machine_check.status driving run advancement, and HITL pauses.

RENDER/DELIVERY success paths need real ffmpeg renders plus a human-approved
episode render version, so they are verified through their deterministic
preflight-failure paths here and flagged as integration verification in the
completion report.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.configuration import ConfigurationService
from local_drama.application.dialogue import DialogueService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
from local_drama.application.worker import LocalMediaWorker
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _project(workspace, database, code: str = "whole_drama", title: str = "整剧编排测试", episode_count: int = 1) -> dict:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title=title,
        episode_count=episode_count,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )


def _episodes(database, workspace, project: dict) -> list[dict]:
    projects = ProjectService(database, workspace.projects_root)
    items: list[dict] = []
    for season in projects.list_seasons(str(project["id"])):
        items.extend(projects.list_episodes(str(season["id"])))
    return items


def _start_and_first_step(service: AutomationWorkflowService, workflow_id: str) -> dict:
    plan = service.plan_workflow(workflow_id)
    run = service.start_run(workflow_id, plan_hash=str(plan["plan_hash"]), idempotency_key=f"run-{uuid.uuid4().hex}")
    # BATCH_AUTOMATED start primes the first task job (QUEUED, claimable by the
    # worker); every later task is advanced by the worker after each Job
    # completes.  No manual step_run call is needed anymore.
    return service.get_run(str(run["id"]))


def _approve_keyframe(database, project_id: str, shot_id: str) -> str:
    asset_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    now = _now()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO media_assets
            (id, project_id, owner_type, owner_id, purpose, media_kind, approved_version_id, version_counter,
             metadata_json, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 'SHOT', ?, 'KEYFRAME', 'IMAGE', ?, 1, '{}', ?, ?, 'test', 1, 'v2')""",
            (asset_id, project_id, shot_id, version_id, now, now),
        )
        connection.execute(
            """INSERT INTO media_versions
            (id, media_asset_id, version_no, take_no, stage, rel_path, mime_type, byte_size, sha256,
             integrity_status, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, 1, 'KEYFRAME', 'media/kf.png', 'image/png', 0, ?, 'VERIFIED', ?, ?, 'test', 1, 'v2')""",
            (version_id, asset_id, "0" * 64, now, now),
        )
    return version_id


def _action_workflow(service: AutomationWorkflowService, project_id: str, action: str, episode_id: str, code: str, *, extra_actions: tuple[str, ...] = ("KEYFRAME_CHECK",)) -> dict:
    batch_items = [{"key": f"ep:{action}", "payload": {"action": action, "episode_id": episode_id}}]
    for index, extra in enumerate(extra_actions, start=1):
        batch_items.append({"key": f"ep:{action}:{index}", "payload": {"action": extra, "episode_id": episode_id}})
    task_cap = len(batch_items) + 1
    return service.create_workflow(
        project_id,
        code=code,
        title=f"{action} 自动化验证",
        mode="BATCH_AUTOMATED",
        nodes=[{"id": "node", "type": "TASK"}],
        batch_items=batch_items,
        conditions=[
            {"field": "machine_check.status", "operator": "EQ", "value": "NEEDS_HITL", "action": "PAUSE_HITL"},
            {"field": "machine_check.status", "operator": "IN", "value": ["FAIL", "FAILED", "BLOCKED"], "action": "PAUSE_HITL"},
        ],
        max_iterations=task_cap,
        max_tasks=task_cap,
        max_disk_bytes=1 << 20,
        human_gate="ON_CONDITION",
        repeat_batch=False,
    )


def _read_report(workspace, outcome: dict) -> dict:
    assert outcome is not None
    assert outcome["result"]["job_state"] == "SUCCEEDED"
    assert outcome["artifact"]["kind"] == "AUTOMATION_TASK_REPORT"
    path = workspace.work_root / outcome["artifact"]["sandbox_rel_path"]
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Template expansion
# ---------------------------------------------------------------------------


def test_whole_drama_template_expands_episodes_in_order(workspace, database) -> None:
    project = _project(workspace, database, episode_count=2)
    episode_ids = [str(episode["id"]) for episode in _episodes(database, workspace, project)]
    assert len(episode_ids) == 2
    service = AutomationWorkflowService(database)
    workflow = service.create_from_template(str(project["id"]), template_code="WHOLE_DRAMA", title="一键成剧")
    assert workflow["code"] == "WHOLE_DRAMA"
    assert workflow["mode"] == "BATCH_AUTOMATED"
    assert workflow["ai_approval_allowed"] is False
    assert workflow["local_only"] is True
    definition = workflow["definition"]
    assert definition["human_gate"] == "ON_CONDITION"
    assert definition["repeat_batch"] is False
    assert definition["node_gate"] is False
    assert definition["nodes"] == [{"id": "whole-drama", "type": "WHOLE_DRAMA_TASK", "requires_human_approval": False, "metadata": {}}]
    assert definition["conditions"] == [
        {"field": "machine_check.status", "operator": "EQ", "value": "NEEDS_HITL", "action": "PAUSE_HITL"},
        {"field": "machine_check.status", "operator": "IN", "value": ["FAIL", "FAILED", "BLOCKED"], "action": "PAUSE_HITL"},
    ]
    items = definition["batch_items"]
    assert len(items) == 8  # 2 episodes x 4 steps
    assert len({item["key"] for item in items}) == 8
    for index, episode_id in enumerate(episode_ids):
        base = index * 4
        assert items[base]["key"] == f"EPISODE_{index + 1:03d}:KEYFRAME_CHECK"
        assert items[base]["payload"] == {"action": "KEYFRAME_CHECK", "episode_id": episode_id, "requires_human_approval": True}
        assert items[base + 1]["payload"] == {"action": "TTS_BATCH", "episode_id": episode_id}
        assert items[base + 2]["payload"] == {"action": "RENDER", "episode_id": episode_id}
        assert items[base + 3]["payload"] == {"action": "DELIVERY", "episode_id": episode_id}
    assert definition["max_iterations"] == 9  # len(items) + 1 lets the final advance end as SUCCEEDED
    assert definition["max_tasks"] == 9
    assert definition["max_disk_bytes"] == min(1 << 50, 2 * 2 * 1024 * 1024 * 1024)
    # plan/start must be usable on the expanded definition
    plan = service.plan_workflow(str(workflow["id"]))
    assert plan["batch_count"] == 8
    assert plan["estimated_tasks"] == 8
    run = service.start_run(str(workflow["id"]), plan_hash=str(plan["plan_hash"]), idempotency_key="template-run")
    assert run["status"] == "RUNNING"
    assert run["ai_scores_can_approve"] is False
    # BATCH_AUTOMATED start primes the first task job so a worker/scheduler can
    # drive the run without a manual step; HITL gates stay declarative.
    assert run["task_count"] == 1
    assert run["tasks"][0]["job_id"]
    assert run["tasks"][0]["item"]["payload"]["action"] == "KEYFRAME_CHECK"


def test_whole_drama_template_errors_and_list(workspace, database) -> None:
    project = _project(workspace, database)
    service = AutomationWorkflowService(database)
    with pytest.raises(DomainRuleError) as error:
        service.create_from_template(str(project["id"]), template_code="UNKNOWN", title="x")
    assert error.value.code == "AUTOMATION_TEMPLATE_UNSUPPORTED"
    with pytest.raises(DomainRuleError) as error:
        service.create_from_template(str(uuid.uuid4()), template_code="WHOLE_DRAMA", title="x")
    assert error.value.code == "PROJECT_NOT_FOUND"
    first = service.create_from_template(str(project["id"]), template_code="WHOLE_DRAMA", title="首次创建")
    assert first["code"] == "WHOLE_DRAMA"
    with pytest.raises(DomainRuleError) as error:
        service.create_from_template(str(project["id"]), template_code="WHOLE_DRAMA", title="重复创建")
    assert error.value.code == "AUTOMATION_WORKFLOW_CODE_EXISTS"
    templates = service.list_templates()
    assert [item["code"] for item in templates] == ["WHOLE_DRAMA"]
    assert templates[0]["title"] == "整剧一键编排"
    assert "关键帧" in templates[0]["description"]


def test_whole_drama_template_api(workspace, database) -> None:
    project = _project(workspace, database)
    app = create_app(workspace)
    with TestClient(app) as client:
        listed = client.get("/api/v1/automation-templates")
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert [item["code"] for item in items] == ["WHOLE_DRAMA"]
        created = client.post(
            f"/api/v1/projects/{project['id']}/automation-workflows:from-template",
            json={"template_code": "WHOLE_DRAMA", "title": "API 一键成剧"},
        )
        assert created.status_code == 201, created.text
        workflow = created.json()["workflow"]
        assert workflow["code"] == "WHOLE_DRAMA"
        assert workflow["mode"] == "BATCH_AUTOMATED"
        unsupported = client.post(
            f"/api/v1/projects/{project['id']}/automation-workflows:from-template",
            json={"template_code": "NOPE", "title": "x"},
        )
        assert unsupported.status_code == 422
        assert unsupported.json()["error"]["code"] == "AUTOMATION_TEMPLATE_UNSUPPORTED"
        missing_project = client.post(
            "/api/v1/projects/00000000-0000-0000-0000-000000000000/automation-workflows:from-template",
            json={"template_code": "WHOLE_DRAMA", "title": "x"},
        )
        assert missing_project.status_code == 404
        assert missing_project.json()["error"]["code"] == "PROJECT_NOT_FOUND"


# ---------------------------------------------------------------------------
# Executor: KEYFRAME_CHECK
# ---------------------------------------------------------------------------


def test_automation_worker_keyframe_check_needs_hitl_pauses_run(workspace, database) -> None:
    project = _project(workspace, database)
    episode = _episodes(database, workspace, project)[0]
    projects = ProjectService(database, workspace.projects_root)
    projects.create_shot(str(episode["id"]), "SH-001", 4_000)
    projects.create_shot(str(episode["id"]), "SH-002", 4_000)
    service = AutomationWorkflowService(database)
    workflow = service.create_from_template(str(project["id"]), template_code="WHOLE_DRAMA", title="关键帧门禁")
    run = _start_and_first_step(service, str(workflow["id"]))
    assert run["tasks"][0]["item_key"].endswith(":KEYFRAME_CHECK")
    job_id = run["tasks"][0]["job_id"]
    assert run["tasks"][0]["job_state"] == "QUEUED"
    outcome = LocalMediaWorker(database, workspace).run_once("worker-keyframe")
    assert outcome is not None and outcome["job"]["id"] == job_id
    report = _read_report(workspace, outcome)
    assert report["action"] == "KEYFRAME_CHECK"
    assert report["status"] == "NEEDS_HITL"
    assert report["schema_version"] == "localdrama.automation-task-report.v1"
    assert [item["shot_code"] for item in report["machine_check"]["missing_shots"]] == ["SH-001", "SH-002"]
    assert report["machine_check"]["checked_shots"] == 2
    advanced = service.get_run(str(run["id"]))
    assert advanced["status"] == "PAUSED_HITL"
    assert advanced["human_approval_status"] == "PENDING"
    assert advanced["pending_gate"]["reason"] == "DECLARATIVE_CONDITION"
    assert advanced["machine_context"]["machine_check"]["status"] == "NEEDS_HITL"
    # The next task (TTS_BATCH) was created but is parked behind the HITL gate.
    assert advanced["tasks"][1]["item_key"].endswith(":TTS_BATCH")
    assert advanced["tasks"][1]["job_state"] == "NEEDS_ATTENTION"


def test_automation_worker_keyframe_check_pass_advances_run(workspace, database) -> None:
    project = _project(workspace, database)
    episode = _episodes(database, workspace, project)[0]
    projects = ProjectService(database, workspace.projects_root)
    shot = projects.create_shot(str(episode["id"]), "SH-001", 4_000)
    _approve_keyframe(database, str(project["id"]), str(shot["id"]))
    service = AutomationWorkflowService(database)
    workflow = service.create_from_template(str(project["id"]), template_code="WHOLE_DRAMA", title="关键帧通过")
    run = _start_and_first_step(service, str(workflow["id"]))
    outcome = LocalMediaWorker(database, workspace).run_once("worker-keyframe-pass")
    report = _read_report(workspace, outcome)
    assert report["status"] == "PASS"
    assert report["machine_check"]["missing_shots"] == []
    advanced = service.get_run(str(run["id"]))
    assert advanced["status"] == "RUNNING"
    assert advanced["tasks"][1]["item_key"].endswith(":TTS_BATCH")
    assert advanced["tasks"][1]["job_state"] == "QUEUED"


# ---------------------------------------------------------------------------
# Executor: TTS_BATCH
# ---------------------------------------------------------------------------


def _insert_asset(database, project_id: str, code: str, name: str) -> str:
    asset_id = str(uuid.uuid4())
    now = _now()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO story_assets
            (id, project_id, kind, code, name, description, canonical_media_version_id, extra_json, status,
             created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 'CHARACTER', ?, ?, '', NULL, '{}', 'ACTIVE', ?, ?, 'test', 1, 'v2')""",
            (asset_id, project_id, code, name, now, now),
        )
    return asset_id


def _bind_tts_profile(database, voice_profile_version_id: str, profile_id: str = "tts-profile-v1") -> None:
    with database.transaction() as connection:
        connection.execute("INSERT INTO execution_profiles (id, code, title) VALUES ('tts-profile', 'tts-test', 'TTS test')")
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id, execution_profile_id, version_no, capability, model_bundle_json, input_contract_json,
             parameter_schema_json, status, capability_json, output_contract_json, resource_policy_json)
            VALUES (?, ?, 1, 'TTS', '{}', '{}', '{}', 'PUBLISHED', '{}', '{}', '{}')""",
            (profile_id, "tts-profile"),
        )
        connection.execute(
            "UPDATE voice_profile_versions SET provider_profile_version_id=? WHERE id=?",
            (profile_id, voice_profile_version_id),
        )


def _create_voice(client, project_id: str, code: str, title: str, voice_ref: str) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/voice-profile-versions",
        json={
            "code": code,
            "title": title,
            "voice_ref": voice_ref,
            "license_status": "USER_OWNED",
            "license_evidence_path_rel": "00_admin/voice-license.json",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["voice_profile"]


def test_automation_worker_tts_batch_submits_and_reports_counts(workspace, database) -> None:
    if not hasattr(DialogueService, "submit_episode_tts_batch"):
        pytest.skip("submit_episode_tts_batch not landed")
    project = _project(workspace, database, code="auto_tts", title="自动化 TTS")
    episode = _episodes(database, workspace, project)[0]
    project_id, episode_id = str(project["id"]), str(episode["id"])
    root = workspace.projects_root / str(project["root_rel"])
    evidence = root / "00_admin" / "voice-license.json"
    evidence.write_text('{"owner":"test","scope":"automation tts"}\n', encoding="utf-8", newline="")
    guilan_id = _insert_asset(database, project_id, "CHAR-001", "周桂兰")
    with TestClient(create_app(workspace)) as client:
        voice = _create_voice(client, project_id, "voice-a", "Voice A", "sapi:TestVoice")
        _bind_tts_profile(database, voice["id"])
        binding = client.post(
            f"/api/v1/projects/{project_id}/character-voice-bindings",
            json={"character_asset_id": guilan_id, "voice_profile_version_id": voice["id"]},
        )
        assert binding.status_code == 201, binding.text
        for code, text in [("DLG-001", "谁在里面？"), ("DLG-002", "是我。")]:
            line = client.post(f"/api/v1/episodes/{episode_id}/dialogue-lines", json={"code": code, "speaker": "周桂兰", "text": text})
            assert line.status_code == 201, line.text
    service = AutomationWorkflowService(database)
    workflow = _action_workflow(service, project_id, "TTS_BATCH", episode_id, "tts-auto", extra_actions=())
    run = _start_and_first_step(service, str(workflow["id"]))
    assert run["tasks"][0]["item_key"].endswith("ep:TTS_BATCH")
    outcome = LocalMediaWorker(database, workspace).run_once("worker-tts")
    report = _read_report(workspace, outcome)
    assert report["action"] == "TTS_BATCH"
    assert report["machine_check"]["counts"] == {"submitted": 2, "skipped": 0, "failed": 0}
    assert report["machine_check"]["job_count"] == 2
    with database.connect() as connection:
        jobs = connection.execute(
            "SELECT id, state, idempotency_key FROM jobs WHERE type='TTS_GENERATION' AND project_id=?", (project_id,)
        ).fetchall()
        assert len(jobs) == 2
        assert all(job["state"] == "QUEUED" for job in jobs)
    advanced = service.get_run(str(run["id"]))
    # Single-item finite batch: the run completes when the last task Job is
    # handed off; the TTS outcome itself is carried by the task report
    # artifact asserted above (the Job remains the audit/execution hand-off).
    assert advanced["status"] == "SUCCEEDED"
    assert advanced["tasks"][0]["job_id"]


# ---------------------------------------------------------------------------
# Executor: RENDER preflight-failure paths (success needs real ffmpeg; see report)
# ---------------------------------------------------------------------------


def test_automation_worker_render_reports_missing_timeline(workspace, database) -> None:
    project = _project(workspace, database, code="auto_render")
    episode = _episodes(database, workspace, project)[0]
    service = AutomationWorkflowService(database)
    workflow = _action_workflow(service, str(project["id"]), "RENDER", str(episode["id"]), "render-no-timeline")
    run = _start_and_first_step(service, str(workflow["id"]))
    outcome = LocalMediaWorker(database, workspace).run_once("worker-render")
    report = _read_report(workspace, outcome)
    assert report["action"] == "RENDER"
    assert report["status"] == "FAIL"
    assert report["machine_check"]["code"] == "RENDER_NO_TIMELINE"
    assert report["machine_check"]["ok"] is False
    advanced = service.get_run(str(run["id"]))
    assert advanced["status"] == "PAUSED_HITL"
    assert advanced["machine_context"]["machine_check"]["code"] == "RENDER_NO_TIMELINE"
    assert advanced["tasks"][1]["job_state"] == "NEEDS_ATTENTION"


def test_automation_worker_render_reports_timeline_without_video(workspace, database) -> None:
    project = _project(workspace, database, code="auto_render2")
    episode = _episodes(database, workspace, project)[0]
    timeline = TimelineService(database, workspace).create_timeline_revision(
        str(episode["id"]),
        items=[{"start_us": 0, "end_us": 1_000_000, "track_type": "AUDIO"}],
        input_snapshot={"schema_version": "localdrama.test.v1"},
        status="DRAFT",
        actor="test",
    )
    assert timeline["id"]
    service = AutomationWorkflowService(database)
    workflow = _action_workflow(service, str(project["id"]), "RENDER", str(episode["id"]), "render-no-video")
    _start_and_first_step(service, str(workflow["id"]))
    outcome = LocalMediaWorker(database, workspace).run_once("worker-render2")
    report = _read_report(workspace, outcome)
    assert report["status"] == "FAIL"
    assert report["machine_check"]["code"] == "TIMELINE_VIDEO_REQUIRED"


# ---------------------------------------------------------------------------
# Executor: DELIVERY preflight paths (success needs a human-approved render)
# ---------------------------------------------------------------------------


def test_automation_worker_delivery_skips_without_target(workspace, database) -> None:
    project = _project(workspace, database, code="auto_delivery")
    episode = _episodes(database, workspace, project)[0]
    service = AutomationWorkflowService(database)
    workflow = _action_workflow(service, str(project["id"]), "DELIVERY", str(episode["id"]), "delivery-no-target", extra_actions=("KEYFRAME_CHECK", "TTS_BATCH"))
    run = _start_and_first_step(service, str(workflow["id"]))
    outcome = LocalMediaWorker(database, workspace).run_once("worker-delivery")
    report = _read_report(workspace, outcome)
    assert report["action"] == "DELIVERY"
    assert report["status"] == "SKIPPED"
    assert report["machine_check"]["code"] == "DELIVERY_NO_TARGET"
    # SKIPPED is not a gate condition: the run keeps going with the next task.
    advanced = service.get_run(str(run["id"]))
    assert advanced["status"] == "RUNNING"
    assert advanced["tasks"][1]["job_state"] == "QUEUED"


def test_automation_worker_delivery_fails_without_render(workspace, database) -> None:
    project = _project(workspace, database, code="auto_delivery2")
    episode = _episodes(database, workspace, project)[0]
    ConfigurationService(database).create_delivery_target(
        str(project["id"]), "out", "输出目录", "LOCAL_FILESYSTEM", {"path_rel": "06_delivery"}
    )
    service = AutomationWorkflowService(database)
    workflow = _action_workflow(service, str(project["id"]), "DELIVERY", str(episode["id"]), "delivery-no-render")
    run = _start_and_first_step(service, str(workflow["id"]))
    outcome = LocalMediaWorker(database, workspace).run_once("worker-delivery2")
    report = _read_report(workspace, outcome)
    assert report["status"] == "FAIL"
    assert report["machine_check"]["code"] == "DELIVERY_NO_RENDER"
    advanced = service.get_run(str(run["id"]))
    assert advanced["status"] == "PAUSED_HITL"


def test_automation_worker_delivery_reports_render_approval_required(workspace, database) -> None:
    project = _project(workspace, database, code="auto_delivery3")
    episode = _episodes(database, workspace, project)[0]
    ConfigurationService(database).create_delivery_target(
        str(project["id"]), "out", "输出目录", "LOCAL_FILESYSTEM", {"path_rel": "06_delivery"}
    )
    timeline = TimelineService(database, workspace).create_timeline_revision(
        str(episode["id"]),
        items=[{"start_us": 0, "end_us": 1_000_000, "track_type": "AUDIO"}],
        input_snapshot={"schema_version": "localdrama.test.v1"},
        status="DRAFT",
        actor="test",
    )
    now = _now()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO episode_render_versions
            (id, episode_id, timeline_revision_id, rel_path, sha256, probe_json, integrity_status,
             created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, ?, 'renders/fake.mp4', ?, '{}', 'VERIFIED', ?, ?, 'test', 1, 'v2')""",
            (str(uuid.uuid4()), str(episode["id"]), str(timeline["id"]), "0" * 64, now, now),
        )
    service = AutomationWorkflowService(database)
    workflow = _action_workflow(service, str(project["id"]), "DELIVERY", str(episode["id"]), "delivery-approval")
    _start_and_first_step(service, str(workflow["id"]))
    outcome = LocalMediaWorker(database, workspace).run_once("worker-delivery3")
    report = _read_report(workspace, outcome)
    # build_delivery is a hard human gate: a machine-verified render is not an
    # approval, so the automated DELIVERY step reports FAIL and pauses the run.
    assert report["status"] == "FAIL"
    assert report["machine_check"]["code"] == "EPISODE_RENDER_APPROVAL_REQUIRED"


# ---------------------------------------------------------------------------
# Executor: unsupported action / malformed payloads fail the Job
# ---------------------------------------------------------------------------


def test_automation_worker_rejects_unsupported_action(workspace, database) -> None:
    project = _project(workspace, database, code="auto_bad_action")
    episode = _episodes(database, workspace, project)[0]
    service = AutomationWorkflowService(database)
    workflow = service.create_workflow(
        str(project["id"]),
        code="bad-action",
        title="不支持的动作",
        mode="BATCH_AUTOMATED",
        nodes=[{"id": "node", "type": "TASK"}],
        batch_items=[{"key": "ep:WARP", "payload": {"action": "WARP", "episode_id": str(episode["id"])}}],
        conditions=[],
        max_iterations=2,
        max_tasks=2,
        max_disk_bytes=1 << 20,
        human_gate="NONE",
        repeat_batch=False,
    )
    run = _start_and_first_step(service, str(workflow["id"]))
    assert run["tasks"][0]["job_state"] == "QUEUED"
    outcome = LocalMediaWorker(database, workspace).run_once("worker-bad-action")
    assert outcome is not None
    assert outcome["result"]["job_state"] == "FAILED"
    assert outcome["error"] == "AUTOMATION_ACTION_UNSUPPORTED"


# ---------------------------------------------------------------------------
# Executor: full WHOLE_DRAMA chain walk with HITL resume
# ---------------------------------------------------------------------------


def test_automation_whole_drama_chain_walk_with_hitl_resume(workspace, database) -> None:
    """One approved keyframe episode through KEYFRAME_CHECK→TTS_BATCH→RENDER→DELIVERY.

    RENDER fails the machine preflight (no timeline) and pauses the run; the
    human approves the pause; DELIVERY then runs and skips (no target).  The
    final advance observes finite-batch exhaustion and ends the run SUCCEEDED.
    """
    project = _project(workspace, database, code="whole_drama_chain")
    episode = _episodes(database, workspace, project)[0]
    projects = ProjectService(database, workspace.projects_root)
    shot = projects.create_shot(str(episode["id"]), "SH-001", 4_000)
    _approve_keyframe(database, str(project["id"]), str(shot["id"]))
    service = AutomationWorkflowService(database)
    workflow = service.create_from_template(str(project["id"]), template_code="WHOLE_DRAMA", title="整剧链条")
    run = _start_and_first_step(service, str(workflow["id"]))

    # 1) KEYFRAME_CHECK passes -> TTS_BATCH job becomes claimable.
    outcome = LocalMediaWorker(database, workspace).run_once("worker-chain-1")
    assert _read_report(workspace, outcome)["status"] == "PASS"
    run = service.get_run(str(run["id"]))
    assert run["status"] == "RUNNING"
    assert run["tasks"][1]["item_key"].endswith(":TTS_BATCH")

    # 2) TTS_BATCH runs with no dialogue -> empty batch counts, run continues.
    outcome = LocalMediaWorker(database, workspace).run_once("worker-chain-2")
    report = _read_report(workspace, outcome)
    assert report["action"] == "TTS_BATCH"
    assert report["machine_check"]["counts"] == {"submitted": 0, "skipped": 0, "failed": 0}
    run = service.get_run(str(run["id"]))
    assert run["status"] == "RUNNING"
    assert run["tasks"][2]["item_key"].endswith(":RENDER")

    # 3) RENDER has no timeline revision -> FAIL report pauses the run.
    outcome = LocalMediaWorker(database, workspace).run_once("worker-chain-3")
    assert _read_report(workspace, outcome)["machine_check"]["code"] == "RENDER_NO_TIMELINE"
    run = service.get_run(str(run["id"]))
    assert run["status"] == "PAUSED_HITL"
    assert run["tasks"][3]["item_key"].endswith(":DELIVERY")
    assert run["tasks"][3]["job_state"] == "NEEDS_ATTENTION"

    # 4) Human approves -> DELIVERY executes and skips (no delivery target).
    resumed = service.resume_run(str(run["id"]), decision="HUMAN_APPROVED", note="人工确认后继续")
    assert resumed["status"] == "RUNNING"
    outcome = LocalMediaWorker(database, workspace).run_once("worker-chain-4")
    report = _read_report(workspace, outcome)
    assert report["action"] == "DELIVERY"
    assert report["machine_check"]["code"] == "DELIVERY_NO_TARGET"
    finished = service.get_run(str(run["id"]))
    assert finished["status"] == "SUCCEEDED"
    assert finished["task_count"] == 4
    # Task-level machine contexts record the context passed when each task was
    # created: task4 (DELIVERY) carries the RENDER failure that paused the run.
    # The run-level machine context reflects the final machine state (the
    # DELIVERY SKIPPED outcome), while the full report lives in the artifact.
    assert finished["machine_context"]["machine_check"]["code"] == "DELIVERY_NO_TARGET"
    assert finished["tasks"][3]["machine_context"]["machine_check"]["code"] == "RENDER_NO_TIMELINE"

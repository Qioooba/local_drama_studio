from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.breakdown_apply import BreakdownApplyService
from local_drama.application.configuration import ConfigurationService
from local_drama.application.dialogue import DialogueService
from local_drama.application.documents import DocumentImportService
from local_drama.application.episode_front_half_actions import EpisodeFrontHalfActionService
from local_drama.application.episode_production_runs import (
    ACTION_STAGE,
    FRONT_HALF_ACTIONS,
    STAGE_DEFINITIONS,
    EpisodeProductionRunService,
)
from local_drama.application.episode_worker_actions import EpisodeWorkerActionService
from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
from local_drama.application.worker import LocalMediaWorker
from local_drama.application.worker_handlers.automation_task import run_automation_task
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.atomic import write_atomic


def test_stage_definitions_and_front_half_action_mapping() -> None:
    assert len(STAGE_DEFINITIONS) == 8
    stage_codes = {item[0] for item in STAGE_DEFINITIONS}
    assert "STORY_ANALYSIS" in stage_codes
    assert "ASSET_EXTRACTION" in stage_codes
    assert "ASSET_COMPLETION" in stage_codes
    assert "SHOT_PLANNING" in stage_codes
    assert "SHOT_IMAGE" in stage_codes
    assert "VIDEO" in stage_codes
    assert "AUDIO_SUBTITLE" in stage_codes
    assert "COMPOSE_QC" in stage_codes

    assert ACTION_STAGE["STORY_PARSE"] == "STORY_ANALYSIS"
    assert ACTION_STAGE["SCRIPT_BREAKDOWN"] == "STORY_ANALYSIS"
    assert ACTION_STAGE["ASSET_IDENTITY"] == "ASSET_EXTRACTION"
    assert ACTION_STAGE["ASSET_COMPLETION"] == "ASSET_COMPLETION"
    assert ACTION_STAGE["EPISODE_PLAN"] == "SHOT_PLANNING"
    assert ACTION_STAGE["KEYFRAME_CHECK"] == "SHOT_IMAGE"
    assert ACTION_STAGE["VIDEO_GENERATION"] == "VIDEO"
    assert ACTION_STAGE["QC"] == "COMPOSE_QC"
    assert ACTION_STAGE["TTS_BATCH"] == "AUDIO_SUBTITLE"
    assert ACTION_STAGE["RENDER"] == "COMPOSE_QC"
    assert ACTION_STAGE["DELIVERY"] == "COMPOSE_QC"


def test_front_half_dag_workflow_generation(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="dag_front_half_test", title="DAG Front Half Test", episode_count=1,
        aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]

    service = EpisodeProductionRunService(database, workspace)
    preflight = {
        "input_fingerprint": "a" * 64,
        "tts_enabled": True,
        "include_front_half": True,
        "production_mode": "BALANCED",
        "checkpoint_policy": "ON_EXCEPTION",
        "checks": [{"evidence": {}} for _ in range(6)] + [{"evidence": {"required_free_bytes": 1_000_000}}],
    }
    episode_context = {**episode, "project_id": project["id"]}
    workflow = service._workflow_for_snapshot(episode_context, preflight, actor="test-dag")
    batch_items = workflow["definition"]["batch_items"]
    actions = [item["payload"]["action"] for item in batch_items]

    # Must contain both front-half and back-half actions in DAG order
    assert actions[0] == "STORY_PARSE"
    assert actions[1] == "SCRIPT_BREAKDOWN"
    assert actions[2] == "ASSET_IDENTITY"
    assert actions[3] == "ASSET_COMPLETION"
    assert actions[4] == "EPISODE_PLAN"
    assert actions[5] == "KEYFRAME_CHECK"
    assert actions[6] == "VIDEO_GENERATION"
    assert actions[7] == "QC"
    assert actions[8] == "TTS_BATCH"
    assert actions[9] == "RENDER"
    assert actions[10] == "DELIVERY"


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


def test_full_preflight_uses_authoritative_asset_completion_gate(
    workspace, database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project, episode = _episode(workspace, database, "full_asset_completion_gate")

    def _missing_completion(
        self: EpisodeFrontHalfActionService, episode_id: str,
    ) -> tuple[dict, int]:
        del self, episode_id
        return (
            {
                "status": "NEEDS_HITL",
                "machine_check": {
                    "status": "NEEDS_HITL",
                    "code": "ASSET_COMPLETION_REQUIRED",
                    "detail": "角色尚缺三视图身份包",
                    "missing_asset_ids": ["character-1"],
                    "human_approval_created": False,
                },
            },
            0,
        )

    monkeypatch.setattr(EpisodeFrontHalfActionService, "asset_completion", _missing_completion)
    result = EpisodeProductionRunService(database, workspace).preflight(
        str(episode["id"]), include_front_half=True,
    )

    check = next(item for item in result["checks"] if item["code"] == "ASSET_COMPLETION_REQUIRED")
    assert check["status"] == "BLOCKED"
    assert check["detail"] == "角色尚缺三视图身份包"
    assert check["evidence"]["missing_asset_ids"] == ["character-1"]
    assert "ASSET_COMPLETION_REQUIRED" in {item["code"] for item in result["blockers"]}


def _commit_source(workspace, database, project_id: str, source_path: Path) -> dict:
    source_path.write_text("第一场\n\n角色甲走进房间。", encoding="utf-8")
    documents = DocumentImportService(database, workspace)
    imported = documents.import_document(project_id, source_path)
    return documents.commit(str(imported["import_session_id"]), str(imported["preview_hash"]))


def test_front_half_only_start_is_fail_closed_and_idempotent(
    workspace, database, tmp_path: Path,
) -> None:
    project, episode = _episode(workspace, database, "front_half_start")
    service = EpisodeProductionRunService(database, workspace)

    with pytest.raises(DomainRuleError) as missing_source:
        service.start(
            str(episode["id"]), idempotency_key="front-half-missing-source",
            front_half_only=True,
        )
    assert missing_source.value.code == "EPISODE_FRONT_HALF_PREFLIGHT_BLOCKED"
    assert missing_source.value.details["blocker_codes"] == ["FRONT_HALF_SOURCE_NOT_READY"]
    assert JobService(database, workspace).list_jobs(str(project["id"])) == []

    _commit_source(workspace, database, str(project["id"]), tmp_path / "front-half.md")
    first = service.start(
        str(episode["id"]), idempotency_key="front-half-one-run",
        front_half_only=True,
    )
    replay = service.start(
        str(episode["id"]), idempotency_key="front-half-one-run",
        front_half_only=True,
    )

    assert first["id"] == replay["id"]
    assert first["front_half_only"] is True
    assert first["include_front_half"] is True
    assert first["stages"][0]["jobs"][0]["job_id"] == replay["stages"][0]["jobs"][0]["job_id"]
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM automation_workflow_runs WHERE workflow_id=?", (first["automation_workflow_id"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM automation_workflow_run_tasks WHERE run_id=?", (first["id"],),
        ).fetchone()[0] == 1


def test_run_view_exposes_exact_shot_issues_from_durable_task_context(
    workspace, database, tmp_path: Path,
) -> None:
    project, episode = _episode(workspace, database, "run_exact_shot_issue")
    _commit_source(workspace, database, str(project["id"]), tmp_path / "exact-shot.md")
    service = EpisodeProductionRunService(database, workspace)
    run = service.start(
        str(episode["id"]), idempotency_key="exact-shot-issue-run", front_half_only=True,
    )
    task_id = str(run["stages"][0]["jobs"][0]["task_id"])
    context = {
        "status": "NEEDS_ATTENTION",
        "machine_check": {
            "status": "NEEDS_ATTENTION",
            "blocked_shots": [
                {
                    "shot_id": "shot-7",
                    "shot_code": "SH-007",
                    "status": "BLOCKED",
                    "code": "VIDEO_CANDIDATE_REQUIRED",
                    "job_id": "job-7",
                }
            ],
        },
    }
    with database.transaction() as connection:
        connection.execute(
            "UPDATE automation_workflow_run_tasks SET machine_context_json=? WHERE id=?",
            (json.dumps(context, ensure_ascii=False), task_id),
        )

    refreshed = service.get(str(run["id"]), include_jobs=True)
    issue = refreshed["stages"][0]["issues"][0]
    assert issue == {
        "shot_id": "shot-7",
        "shot_code": "SH-007",
        "status": "BLOCKED",
        "code": "VIDEO_CANDIDATE_REQUIRED",
        "job_id": "job-7",
    }


def test_real_front_half_worker_reports_and_parks_missing_human_decision(
    workspace, database, tmp_path: Path,
) -> None:
    project, episode = _episode(workspace, database, "front_half_worker")
    _commit_source(workspace, database, str(project["id"]), tmp_path / "worker-source.txt")
    service = EpisodeProductionRunService(database, workspace)
    run = service.start(
        str(episode["id"]), idempotency_key="front-half-worker-run",
        front_half_only=True,
    )
    worker = LocalMediaWorker(database, workspace)

    story = worker.run_once("front-half-worker")
    assert story is not None and story.get("error") is None
    story_report_path = workspace.work_root / str(story["artifact"]["sandbox_rel_path"])
    story_report = json.loads(story_report_path.read_text(encoding="utf-8"))
    assert story_report["action"] == "STORY_PARSE"
    assert story_report["status"] == "PASS"
    assert story_report["machine_check"]["code"] == "SOURCE_COMMIT_VERIFIED"
    assert story_report["produced"]["writes"] == []

    breakdown = worker.run_once("front-half-worker")
    assert breakdown is not None and breakdown.get("error") is None
    breakdown_report_path = workspace.work_root / str(breakdown["artifact"]["sandbox_rel_path"])
    breakdown_report = json.loads(breakdown_report_path.read_text(encoding="utf-8"))
    assert breakdown_report["action"] == "SCRIPT_BREAKDOWN"
    assert breakdown_report["status"] == "NEEDS_HITL"
    assert breakdown_report["machine_check"]["code"] == "BREAKDOWN_DRAFT_REQUIRED"
    assert breakdown_report["machine_check"]["human_approval_created"] is False

    paused = service.automation.get_run(str(run["id"]))
    assert paused["status"] == "PAUSED_HITL"
    assert paused["pending_gate"]["reason"] == "DECLARATIVE_CONDITION"
    assert paused["tasks"][2]["item"]["payload"]["action"] == "ASSET_IDENTITY"
    assert paused["tasks"][2]["job_state"] == "NEEDS_ATTENTION"
    assert worker.run_once("front-half-worker") is None
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM script_breakdown_drafts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM story_asset_proposals").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM shots WHERE episode_id=?", (episode["id"],)).fetchone()[0] == 0


def test_front_half_recovery_marks_old_reports_stale_and_preserves_them(
    workspace, database, tmp_path: Path,
) -> None:
    project, episode = _episode(workspace, database, "front_half_stale")
    committed = _commit_source(workspace, database, str(project["id"]), tmp_path / "stale-source.md")
    service = EpisodeProductionRunService(database, workspace)
    run = service.start(
        str(episode["id"]), idempotency_key="front-half-stale-run",
        front_half_only=True,
    )
    worker = LocalMediaWorker(database, workspace)
    story = worker.run_once("front-half-stale-worker")
    breakdown = worker.run_once("front-half-stale-worker")
    assert story is not None and breakdown is not None
    old_report = workspace.work_root / str(story["artifact"]["sandbox_rel_path"])
    old_report_hash = str(story["artifact"]["sha256"])
    old_fingerprint = str(service.get(str(run["id"]))["input_fingerprint"])

    draft_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO script_breakdown_drafts
            (id,project_id,source_document_version_id,import_session_id,draft_json,confidence_json,
             status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,'DRAFT_READY',?,?, 'test-human',1,'v2')""",
            (
                draft_id,
                project["id"],
                committed["source_document_version_id"],
                committed["id"],
                json.dumps({"scenes": []}),
                json.dumps({}),
                now,
                now,
            ),
        )

    recovered = service.recover(str(run["id"]))

    assert recovered["recovery"]["input_fingerprint"] != old_fingerprint
    assert set(recovered["recovery"]["stale_completed_task_ids"]) == {
        str(run["stages"][0]["jobs"][0]["task_id"]),
        str(service.automation.get_run(str(run["id"]))["tasks"][1]["id"]),
    }
    assert len(recovered["recovery"]["refreshed_task_ids"]) == 1
    assert recovered["run"]["status"] == "PAUSED_HITL"
    assert old_report.is_file()
    story_job = JobService(database, workspace).get_job(str(story["job"]["id"]))
    assert story_job["attempts"][0]["artifacts"][0]["sha256"] == old_report_hash


def test_asset_identity_handler_never_auto_decides_pending_proposal(
    workspace, database, tmp_path: Path,
) -> None:
    project, episode = _episode(workspace, database, "front_half_asset_hitl")
    committed = _commit_source(workspace, database, str(project["id"]), tmp_path / "asset-hitl.md")
    draft_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    draft = {
        "scenes": [
            {
                "scene_no": 1,
                "title": "室内",
                "summary": "角色甲进入房间",
                "characters": ["角色甲"],
                "shots": [
                    {
                        "shot_no": 1,
                        "visual": "角色甲进入房间",
                        "action": "进入",
                        "dialogue": "",
                        "duration_seconds": 4,
                    }
                ],
            }
        ]
    }
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO script_breakdown_drafts
            (id,project_id,source_document_version_id,import_session_id,draft_json,confidence_json,
             status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,'DRAFT_READY',?,?, 'test-llm',1,'v2')""",
            (
                draft_id,
                project["id"],
                committed["source_document_version_id"],
                committed["id"],
                json.dumps(draft, ensure_ascii=False),
                json.dumps({"source_passages": []}),
                now,
                now,
            ),
        )
    BreakdownApplyService(database, workspace).apply_draft(draft_id, str(episode["id"]), actor="test-human")
    automation = AutomationWorkflowService(database)
    workflow = automation.create_workflow(
        str(project["id"]),
        code="asset-identity-hitl",
        title="Asset identity HITL",
        mode="BATCH_AUTOMATED",
        nodes=[{"id": "episode", "type": "EPISODE_PRODUCTION_TASK"}],
        batch_items=[
            {
                "key": "identity",
                "payload": {
                    "action": "ASSET_IDENTITY",
                    "episode_id": str(episode["id"]),
                    "front_half_managed": True,
                },
            },
            {
                "key": "completion",
                "payload": {
                    "action": "ASSET_COMPLETION",
                    "episode_id": str(episode["id"]),
                    "front_half_managed": True,
                },
            },
        ],
        conditions=[
            {"field": "machine_check.status", "operator": "EQ", "value": "NEEDS_HITL", "action": "PAUSE_HITL"},
        ],
        max_iterations=3,
        max_tasks=3,
        max_disk_bytes=1_000_000,
        human_gate="ON_CONDITION",
    )
    run = automation.start_run(
        str(workflow["id"]), plan_hash=str(workflow["plan_hash"]), idempotency_key="asset-identity-hitl-run",
    )

    outcome = LocalMediaWorker(database, workspace).run_once("asset-identity-worker")
    assert outcome is not None
    report_path = workspace.work_root / str(outcome["artifact"]["sandbox_rel_path"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["action"] == "ASSET_IDENTITY"
    assert report["status"] == "NEEDS_HITL"
    assert report["machine_check"]["code"] == "ASSET_IDENTITY_DECISION_REQUIRED"
    assert report["machine_check"]["human_approval_created"] is False
    with database.connect() as connection:
        proposal = connection.execute(
            "SELECT status,resolved_asset_id FROM story_asset_proposals WHERE breakdown_draft_id=?", (draft_id,),
        ).fetchone()
        asset_count = connection.execute("SELECT COUNT(*) FROM story_assets WHERE project_id=?", (project["id"],)).fetchone()[0]
    assert dict(proposal) == {"status": "PENDING", "resolved_asset_id": None}
    assert asset_count == 0
    paused = automation.get_run(str(run["id"]))
    assert paused["status"] == "PAUSED_HITL"
    assert paused["tasks"][1]["job_state"] == "NEEDS_ATTENTION"


@pytest.mark.parametrize("action", FRONT_HALF_ACTIONS)
def test_every_front_half_action_has_a_real_idempotent_worker_handler(
    workspace, database, action: str,
) -> None:
    project, episode = _episode(workspace, database, f"handler_{action.lower()}")
    automation = AutomationWorkflowService(database)
    workflow = automation.create_workflow(
        str(project["id"]),
        code=f"handler-{action.lower()}",
        title=f"{action} handler",
        mode="BATCH_AUTOMATED",
        nodes=[{"id": "episode", "type": "EPISODE_PRODUCTION_TASK"}],
        batch_items=[
            {
                "key": action,
                "payload": {
                    "action": action,
                    "episode_id": str(episode["id"]),
                    "front_half_managed": True,
                },
            },
            {"key": "sentinel", "payload": {"action": "KEYFRAME_CHECK", "episode_id": str(episode["id"])}},
        ],
        conditions=[
            {"field": "machine_check.status", "operator": "EQ", "value": "NEEDS_HITL", "action": "PAUSE_HITL"},
        ],
        max_iterations=3,
        max_tasks=3,
        max_disk_bytes=1_000_000,
        human_gate="ON_CONDITION",
    )
    run = automation.start_run(
        str(workflow["id"]), plan_hash=str(workflow["plan_hash"]),
        idempotency_key=f"handler-run-{action.lower()}",
    )
    job = JobService(database, workspace).get_job(str(run["tasks"][0]["job_id"]))
    output_root = workspace.work_root / "front-half-handler-tests" / action.lower()

    def execute_automation() -> tuple[str, str, dict, int]:
        return run_automation_task(
            job,
            output_root,
            worker_id="handler-test",
            work_root=workspace.work_root,
            database=database,
            front_half_actions_factory=lambda: EpisodeFrontHalfActionService(database, workspace),
            episode_worker_actions_factory=lambda: EpisodeWorkerActionService(database, workspace),
            dialogue_factory=lambda: DialogueService(database, workspace, jobs=JobService(database, workspace), media=MediaService(database, workspace)),
            configuration_factory=lambda: ConfigurationService(database),
            timeline_factory=lambda: TimelineService(database, workspace),
            atomic_writer=write_atomic,
        )

    first = execute_automation()
    second = execute_automation()

    assert first[0] == second[0] == "AUTOMATION_TASK_REPORT"
    assert first[2]["action"] == second[2]["action"] == action
    assert first[2]["machine_check"]["code"] == second[2]["machine_check"]["code"]
    assert first[2]["produced"]["writes"] == second[2]["produced"]["writes"] == []


def test_one_failed_shot_retry_does_not_replay_ready_shot(
    workspace, database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = EpisodeWorkerActionService(database, workspace)
    shots = [
        {"id": "shot-ready", "code": "SHOT-READY"},
        {"id": "shot-failed", "code": "SHOT-FAILED"},
    ]
    retried: list[str] = []
    submitted: list[str] = []
    monkeypatch.setattr(service, "_episode", lambda _episode_id: ("project", shots))
    monkeypatch.setattr(service, "_promote_completed_outputs", lambda _jobs: [])
    monkeypatch.setattr(
        service,
        "_variant_jobs",
        lambda shot_id: [] if shot_id == "shot-ready" else [
            {"id": "failed-job", "variant_id": "failed-variant", "state": "FAILED"},
        ],
    )
    monkeypatch.setattr(
        service,
        "_shot_video",
        lambda shot_id: {"media_version_id": "ready-video"} if shot_id == "shot-ready" else None,
    )
    monkeypatch.setattr(service, "_shot_video_count", lambda shot_id: 1 if shot_id == "shot-ready" else 0)
    monkeypatch.setattr(
        service.jobs,
        "retry",
        lambda job_id, actor: retried.append(job_id) or {"id": job_id},
    )
    monkeypatch.setattr(
        service,
        "_submit_shot",
        lambda _project_id, shot, _run_id, _task_id, **_kwargs: submitted.append(str(shot["id"])) or {
            "shot_id": str(shot["id"]), "shot_code": str(shot["code"]), "status": "SUBMITTED", "job_id": "new-job",
        },
    )

    report, produced_bytes = service.video_generation(
        "episode", "run", "task", target_take_count=1,
    )

    assert produced_bytes == 0
    assert report["status"] == "PASS"
    assert retried == ["failed-job"]
    assert submitted == []
    by_shot = {item["shot_id"]: item for item in report["produced"]["items"]}
    assert by_shot["shot-ready"]["status"] == "READY_FOR_QC"
    assert by_shot["shot-failed"]["status"] == "RETRIED"

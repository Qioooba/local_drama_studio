from __future__ import annotations

import json
import subprocess
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
from local_drama.application.reviews import ReviewService
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
    assert ACTION_STAGE["TTS_FINALIZE"] == "AUDIO_SUBTITLE"
    assert ACTION_STAGE["SUBTITLE"] == "AUDIO_SUBTITLE"
    assert ACTION_STAGE["TIMELINE_ASSEMBLY"] == "COMPOSE_QC"
    assert ACTION_STAGE["RENDER"] == "COMPOSE_QC"
    assert ACTION_STAGE["DELIVERY"] == "COMPOSE_QC"


def test_episode_start_replays_before_preflight_and_rejects_payload_mismatch(
    workspace, database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project, episode = _episode(workspace, database, "episode_parent_identity")
    service = EpisodeProductionRunService(database, workspace)
    calls = 0

    def start_once(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"id": "run-stable"}

    monkeypatch.setattr(service, "_start_once", start_once)
    monkeypatch.setattr(service.automation, "get_run", lambda _run_id: {"id": "run-stable"})
    monkeypatch.setattr(service, "_view", lambda run, **_kwargs: dict(run))

    first = service.start(str(episode["id"]), idempotency_key="episode-command", min_free_disk_bytes=1)
    replay = service.start(str(episode["id"]), idempotency_key="episode-command", min_free_disk_bytes=1)

    assert first == {"id": "run-stable", "idempotent_replay": False}
    assert replay == {"id": "run-stable", "idempotent_replay": True}
    assert calls == 1
    with pytest.raises(DomainRuleError) as mismatch:
        service.start(
            str(episode["id"]), idempotency_key="episode-command",
            production_mode="QUALITY", min_free_disk_bytes=1,
        )
    assert mismatch.value.code == "IDEMPOTENCY_PAYLOAD_MISMATCH"


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
    assert actions[5:] == ["KEYFRAME_GENERATION", "KEYFRAME_CHECK", "VIDEO_GENERATION", "QC", "TTS_BATCH", "TTS_FINALIZE", "SUBTITLE", "TIMELINE_ASSEMBLY", "RENDER", "DELIVERY"]
    assert "_V2_" in workflow["code"]
    assert {item["payload"]["audio_strategy"] for item in batch_items} == {"EXTERNAL_TTS"}

    silent = service._workflow_for_snapshot(
        episode_context,
        {**preflight, "tts_enabled": False, "input_fingerprint": "c" * 64},
        actor="test",
    )
    silent_items = silent["definition"]["batch_items"]
    assert not {"TTS_BATCH", "TTS_FINALIZE", "SUBTITLE"}.intersection(
        item["payload"]["action"] for item in silent_items
    )
    assert {item["payload"]["audio_strategy"] for item in silent_items} == {"SILENT"}
    assert silent["definition"]["nodes"][0]["metadata"]["audio_strategy"] == "SILENT"

    readonly = service._workflow_for_snapshot(
        episode_context, {**preflight, "front_half_only": True, "input_fingerprint": "b" * 64}, actor="test",
    )
    assert [item["payload"]["action"] for item in readonly["definition"]["batch_items"]] == actions[:5] + ["KEYFRAME_CHECK"]


def test_operation_workflow_executes_the_previewed_scope_without_a_parallel_executor(
    workspace, database,
) -> None:
    project, episode = _episode(workspace, database, "episode_operation_scope")
    service = EpisodeProductionRunService(database, workspace)
    episode_context = {**episode, "project_id": project["id"]}
    new_take = {
        "operation": "NEW_TAKE",
        "plan_hash": "a" * 64,
        "target_take_count": 1,
        "expected_profile_version_ids": {"shot-2": "profile-2"},
        "expected_input_fingerprints": {"shot-2": "input-2", "shot-3": "input-3"},
        "sets": {
            "reused": [{"shot_id": "shot-1"}],
            "waiting_in_flight": [],
            "retry_original": [],
            "needs_generation": [{"shot_id": "shot-2"}],
            "blocked_by_dependency": [{"shot_id": "shot-3"}],
            "requires_manual_confirmation": [],
            "compose_only": [],
        },
    }

    workflow = service._workflow_for_operation(episode_context, new_take, actor="test")
    items = workflow["definition"]["batch_items"]
    assert len(items) == 1
    payload = items[0]["payload"]
    assert payload["action"] == "VIDEO_GENERATION"
    assert payload["target_shot_ids"] == ["shot-2"]
    assert payload["force_new_take"] is True
    assert payload["expected_profile_version_ids"] == {"shot-2": "profile-2"}
    assert payload["expected_input_fingerprints"] == {"shot-2": "input-2"}
    assert payload["operation_plan_hash"] == "a" * 64

    recompose = {
        "operation": "RECOMPOSE_ONLY",
        "plan_hash": "b" * 64,
        "target_take_count": 1,
        "expected_profile_version_ids": {},
        "sets": {
            "reused": [],
            "waiting_in_flight": [],
            "retry_original": [],
            "needs_generation": [],
            "blocked_by_dependency": [],
            "requires_manual_confirmation": [],
            "compose_only": [{"timeline_revision_id": "timeline-current"}],
        },
    }
    compose_workflow = service._workflow_for_operation(episode_context, recompose, actor="test")
    compose_payload = compose_workflow["definition"]["batch_items"][0]["payload"]
    assert compose_payload == {
        "action": "RENDER",
        "episode_id": str(episode["id"]),
        "operation": "RECOMPOSE_ONLY",
        "operation_plan_hash": "b" * 64,
        "timeline_revision_id": "timeline-current",
        "force_rerender": True,
    }


def test_operation_start_requires_the_current_preview_hash(
    workspace, database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project, episode = _episode(workspace, database, "episode_operation_plan_guard")
    service = EpisodeProductionRunService(database, workspace)
    monkeypatch.setattr(
        EpisodeWorkerActionService,
        "operation_impact",
        lambda *_args, **_kwargs: {"plan_hash": "c" * 64},
    )

    with pytest.raises(DomainRuleError) as stale:
        service._start_operation_once(
            str(episode["id"]),
            operation="RETRY_ORIGINAL",
            target_shot_ids=(),
            target_take_count=service.operation_target_take_count(
                str(episode["id"]), operation="RETRY_ORIGINAL", production_mode="BALANCED"
            ),
            expected_plan_hash="d" * 64,
            expected_episode_revision=int(episode["revision"]),
            tts_enabled=True,
            production_mode="BALANCED",
            checkpoint_policy="ON_EXCEPTION",
            idempotency_key="operation-stale",
            actor="test",
        )
    assert stale.value.code == "EPISODE_OPERATION_PLAN_STALE"


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


def test_runtime_capacity_and_disk_changes_do_not_change_creative_fingerprints(
    workspace, database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project, episode = _episode(workspace, database, "runtime_not_creative")
    observations = iter([
        {"name": "GPU", "total_bytes": 8_000_000_000, "source": "TEST"},
        {"name": "GPU", "total_bytes": 24_000_000_000, "source": "TEST"},
    ])

    def capacity(_self, _project_id):
        return {
            "gpu": next(observations),
            "gpu_active_count": 0,
            "gpu_concurrency_limit": 1,
        }

    free_values = iter([20_000_000_000, 10_000_000_000])
    monkeypatch.setattr("local_drama.application.episode_production_runs.CapacitySnapshotService.inspect", capacity)
    monkeypatch.setattr(
        "local_drama.application.episode_production_runs.shutil.disk_usage",
        lambda _path: type("Usage", (), {"free": next(free_values)})(),
    )
    monkeypatch.setattr(
        "local_drama.application.episode_production_runs._probe_loopback",
        lambda *_args, **_kwargs: ("FAIL", {"reason": "test"}),
    )
    service = EpisodeProductionRunService(database, workspace)
    first = service.preflight(str(episode["id"]), tts_enabled=False, min_free_disk_bytes=1)
    second = service.preflight(str(episode["id"]), tts_enabled=False, min_free_disk_bytes=1)

    assert first["generation_input_fingerprint"] == second["generation_input_fingerprint"]
    assert first["compose_input_fingerprint"] == second["compose_input_fingerprint"]
    assert first["input_fingerprint"] == second["input_fingerprint"]


def _commit_source(workspace, database, project_id: str, source_path: Path) -> dict:
    source_path.write_text("第一场\n\n角色甲走进房间。", encoding="utf-8")
    documents = DocumentImportService(database, workspace)
    imported = documents.import_document(project_id, source_path)
    return documents.commit(str(imported["import_session_id"]), str(imported["preview_hash"]))


def test_front_half_snapshot_ignores_other_episode_draft(workspace, database, tmp_path: Path) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="episode_scoped_fingerprint", title="Episode scoped fingerprint", episode_count=2,
        aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    episodes = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))
    committed = _commit_source(workspace, database, str(project["id"]), tmp_path / "scoped-source.md")
    service = EpisodeFrontHalfActionService(database, workspace)
    before = service.snapshot(str(episodes[0]["id"]))
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO script_breakdown_drafts
            (id,project_id,source_document_version_id,import_session_id,draft_json,confidence_json,
             status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,'DRAFT_READY',?,?,'test',1,'v2')""",
            (
                str(uuid.uuid4()), project["id"], committed["source_document_version_id"], committed["id"],
                json.dumps({"scenes": []}),
                json.dumps({"target_episode_id": str(episodes[1]["id"])}),
                now, now,
            ),
        )

    assert service.snapshot(str(episodes[0]["id"])) == before


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
                json.dumps(
                    {
                        "target_episode_id": str(episode["id"]),
                        "source_paragraph_start": 1,
                        "source_paragraph_end": 2,
                    }
                ),
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

def test_timeline_assembly_action_assembles_then_skips(workspace, database) -> None:
    project, episode = _episode(workspace, database, "handler_timeline_assembly")
    projects_service = ProjectService(database, workspace.projects_root)
    shot = projects_service.create_shot(str(episode["id"]), "S001", 2_000)

    source = workspace.work_root / "handler-timeline-assembly.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=teal:s=160x90:d=2", "-pix_fmt", "yuv420p", "-an", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    media = MediaService(database, workspace).import_file(
        str(project["id"]),
        source,
        purpose="SHOT_VIDEO",
        owner_type="SHOT",
        owner_id=str(shot["id"]),
        media_kind="VIDEO",
        stage="PROXY",
    )
    ReviewService(database, workspace).select_version(str(media["media_version_id"]), "PROXY_WINNER")

    action = "TIMELINE_ASSEMBLY"
    automation = AutomationWorkflowService(database)
    workflow = automation.create_workflow(
        str(project["id"]),
        code="handler-timeline-assembly",
        title="TIMELINE_ASSEMBLY handler",
        mode="BATCH_AUTOMATED",
        nodes=[{"id": "episode", "type": "EPISODE_PRODUCTION_TASK"}],
        batch_items=[
            {"key": action, "payload": {"action": action, "episode_id": str(episode["id"])}},
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
        idempotency_key="handler-run-timeline-assembly",
    )
    job = JobService(database, workspace).get_job(str(run["tasks"][0]["job_id"]))
    output_root = workspace.work_root / "timeline-assembly-handler-tests"

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
    assert first[2]["action"] == "TIMELINE_ASSEMBLY"
    assert first[2]["machine_check"]["status"] == "PASS"
    revision_id = first[2]["machine_check"]["timeline_revision_id"]
    assert revision_id

    second = execute_automation()
    assert second[2]["machine_check"]["status"] == "SKIPPED"
    assert second[2]["machine_check"]["code"] == "TIMELINE_ALREADY_CURRENT"
    assert second[2]["produced"]["timeline_revision_id"] == revision_id


def test_qc_auto_select_fills_empty_selection_and_never_overrides(workspace, database) -> None:
    project, episode = _episode(workspace, database, "qc_auto_select")
    projects_service = ProjectService(database, workspace.projects_root)
    shot = projects_service.create_shot(str(episode["id"]), "S001", 2_000)

    source = workspace.work_root / "qc-auto-select.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=red:s=160x90:d=2", "-pix_fmt", "yuv420p", "-an", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    media = MediaService(database, workspace).import_file(
        str(project["id"]),
        source,
        purpose="SHOT_VIDEO",
        owner_type="SHOT",
        owner_id=str(shot["id"]),
        media_kind="VIDEO",
        stage="PROXY",
    )
    media_version_id = str(media["media_version_id"])
    service = EpisodeWorkerActionService(database, workspace)

    manual_only, _ = service.qc(str(episode["id"]), "run-a", "task-a")
    assert manual_only["produced"]["items"][0]["status"] == "PASS"
    assert "auto_selection" not in manual_only["produced"]["items"][0]
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM selections WHERE media_version_id=?", (media_version_id,)).fetchone()[0] == 0

    first, _ = service.qc(str(episode["id"]), "run-a", "task-a", auto_select=True)
    item = first["produced"]["items"][0]
    assert item["status"] == "PASS"
    assert item["auto_selection"] == {"status": "SELECTED", "selection_type": "PROXY_WINNER"}
    assert first["machine_check"]["auto_selected_shots"] == 1
    with database.connect() as connection:
        rows = connection.execute("SELECT selection_type, created_by FROM selections WHERE media_version_id=?", (media_version_id,)).fetchall()
    assert [tuple(row) for row in rows] == [("PROXY_WINNER", "episode-run-auto")]

    repeat, _ = service.qc(str(episode["id"]), "run-a", "task-a", auto_select=True)
    assert repeat["produced"]["items"][0]["auto_selection"]["status"] == "ALREADY_CURRENT"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM selections WHERE media_version_id=?", (media_version_id,)).fetchone()[0] == 1


def test_frame_bridge_dependency_uses_scene_ids_and_waits_for_explicit_hard_predecessor(workspace, database) -> None:
    service = EpisodeWorkerActionService(database, workspace)
    current = {"id": "shot-2", "code": "S002", "scene_id": None, "fields_json": None}
    previous = {"id": "shot-1", "code": "S001", "scene_id": None}

    assert service._end_frame_chain(current, None, {})["reason"] == "NO_PREDECESSOR"
    assert service._end_frame_chain(current, previous, {}) == {"status": "SKIPPED", "reason": "SCENE_ID_UNKNOWN"}

    current["scene_id"] = "scene-b"
    previous["scene_id"] = "scene-a"
    result = service._end_frame_chain(current, previous, {"environment": "同名文本不能覆盖 scene id"})
    assert result["status"] == "SKIPPED"
    assert result["reason"] == "SCENE_CUT"

    projects = ProjectService(database, workspace.projects_root)
    project, episode = _episode(workspace, database, "hard_bridge_wait")
    first = projects.create_shot(str(episode["id"]), "S001", 2_000)
    second = projects.create_shot(str(episode["id"]), "S002", 2_000)
    with database.transaction() as connection:
        connection.execute("UPDATE shots SET scene_id='stable-scene' WHERE id IN (?,?)", (first["id"], second["id"]))
    transition = TimelineService(database, workspace).create_transition_constraint(
        str(first["id"]), str(second["id"]), "START_FROM_PREVIOUS_LAST", enforcement="HARD"
    )
    episode_shots = service._episode(str(episode["id"]))[1]
    waiting = service._end_frame_chain(
        episode_shots[1], episode_shots[0], {}, {"media_version_id": "current-first"}
    )
    assert waiting == {
        "status": "WAITING",
        "reason": "PREDECESSOR_VIDEO_MISSING",
        "transition_id": transition["id"],
    }


def test_end_frame_role_detects_slot_from_profile_contract() -> None:
    with_slot = {"input_contract_json": '{"input_slots": {"FIRST_FRAME": {"max": 1}, "END_FRAME": {"max": 1}}}'}
    without_slot = {"input_contract_json": '{"input_slots": {"FIRST_FRAME": {"max": 1}, "REFERENCE_IMAGE": {"max": 4}}}'}
    assert EpisodeWorkerActionService._end_frame_role(with_slot) == "END_FRAME"
    assert EpisodeWorkerActionService._end_frame_role(without_slot) is None


def test_last_frame_anchor_reuses_fresh_anchor_before_extracting(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project, _episode_row = _episode(workspace, database, "anchor_reuse")
    shot = projects.create_shot(str(_episode_row["id"]), "S001", 2_000)
    video_source = workspace.work_root / "anchor-reuse-src.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=blue:s=160x90:d=2", "-pix_fmt", "yuv420p", "-an", "-y", str(video_source)],
        check=True,
        capture_output=True,
    )
    video_version = str(MediaService(database, workspace).import_file(
        str(project["id"]), video_source,
        purpose="SHOT_VIDEO", owner_type="SHOT", owner_id=str(shot["id"]), media_kind="VIDEO", stage="PROXY",
    )["media_version_id"])
    image_source = workspace.work_root / "anchor-reuse-frame.png"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=blue:s=160x90:d=1", "-frames:v", "1", "-y", str(image_source)],
        check=True,
        capture_output=True,
    )
    image_version = str(MediaService(database, workspace).import_file(
        str(project["id"]), image_source,
        purpose="FRAME_ANCHOR", owner_type="MEDIA_VERSION", owner_id=video_version, media_kind="IMAGE", stage="PROXY",
    )["media_version_id"])

    service = EpisodeWorkerActionService(database, workspace)
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO frame_anchors (id, source_media_version_id, source_time_us, source_frame_index,
            extracted_media_version_id, role_hint, sha256, approval_id, created_at, updated_at, created_by,
            revision, schema_version, requested_time_us, resolved_time_us, source_sha256, extraction_method, is_stale)
            VALUES ('anchor-existing', ?, 1_000_000, 23, ?, 'LAST_FRAME', ?, NULL,
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test', 1, 'v2', NULL, 1_000_000, ?,
            'FFPROBE_PTS_FRAME_INDEX', 0)""",
            (video_version, image_version, "a" * 64, "b" * 64),
        )
    reused = service._last_frame_anchor(video_version)
    assert reused == {"id": "anchor-existing", "extracted_media_version_id": image_version, "reused": True}

    with database.transaction() as connection:
        connection.execute("UPDATE frame_anchors SET is_stale=1 WHERE id='anchor-existing'")
    extracted: list[str] = []

    def _fake_create(source_media_version_id: str, **_kwargs) -> dict:
        extracted.append(source_media_version_id)
        return {"id": "anchor-new", "extracted_media_version_id": "anchor-image-new"}

    service.timeline.create_frame_anchor = _fake_create  # type: ignore[method-assign]
    fresh = service._last_frame_anchor(video_version)
    assert fresh["id"] == "anchor-new" and fresh["reused"] is False
    assert extracted == [video_version]


class _FakeFinalizeDialogue:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    def submit_episode_tts_batch(self, episode_id: str, *, idempotency_key_prefix: str, actor: str = "local-user") -> dict:
        raise AssertionError("not used by TTS_FINALIZE")

    def finalize_episode_tts_jobs(self, episode_id: str, *, auto_select: bool, actor: str = "episode-run-auto") -> dict:
        self.calls.append({"episode_id": episode_id, "auto_select": auto_select})
        return self.result

    def list_lines(self, episode_id: str) -> list[dict]:
        return []


def _run_tts_finalize_action(database, workspace, project_id: str, episode_id: str, dialogue_port, mode_policy: dict) -> tuple[str, str, dict, int]:
    automation = AutomationWorkflowService(database)
    workflow = automation.create_workflow(
        project_id,
        code="handler-tts-finalize",
        title="TTS_FINALIZE handler",
        mode="BATCH_AUTOMATED",
        nodes=[{"id": "episode", "type": "EPISODE_PRODUCTION_TASK"}],
        batch_items=[
            {"key": "TTS_FINALIZE", "payload": {"action": "TTS_FINALIZE", "episode_id": episode_id, "mode_policy": mode_policy}},
        ],
        conditions=[
            {"field": "machine_check.status", "operator": "EQ", "value": "NEEDS_HITL", "action": "PAUSE_HITL"},
        ],
        max_iterations=2,
        max_tasks=2,
        max_disk_bytes=1_000_000,
        human_gate="ON_CONDITION",
    )
    run = automation.start_run(str(workflow["id"]), plan_hash=str(workflow["plan_hash"]), idempotency_key="handler-run-tts-finalize")
    job = JobService(database, workspace).get_job(str(run["tasks"][0]["job_id"]))
    return run_automation_task(
        job,
        workspace.work_root / "tts-finalize-handler-tests",
        worker_id="handler-test",
        work_root=workspace.work_root,
        database=database,
        front_half_actions_factory=lambda: EpisodeFrontHalfActionService(database, workspace),
        episode_worker_actions_factory=lambda: EpisodeWorkerActionService(database, workspace),
        dialogue_factory=lambda: dialogue_port,
        configuration_factory=lambda: ConfigurationService(database),
        timeline_factory=lambda: TimelineService(database, workspace),
        atomic_writer=write_atomic,
    )


def test_tts_finalize_handler_reports_pass_and_fail(workspace, database) -> None:
    project, episode_row = _episode(workspace, database, "tts_finalize_handler")
    project_id, episode_id = str(project["id"]), str(episode_row["id"])
    good = _FakeFinalizeDialogue(
        {
            "episode_id": "ep-1",
            "succeeded_jobs": 2,
            "finalized": [{"job_id": "j1", "candidate_id": "c1", "dialogue_line_id": "l1", "media_version_id": "m1", "idempotent_replay": False}],
            "auto_selected": [{"dialogue_line_id": "l1", "selection_id": "s1", "candidate_id": "c1"}],
            "failures": [],
        }
    )
    report = _run_tts_finalize_action(database, workspace, project_id, episode_id, good, {"auto_select_videos": True})[2]
    assert report["machine_check"]["status"] == "PASS"
    assert report["machine_check"]["auto_selected_count"] == 1
    assert good.calls == [{"episode_id": episode_id, "auto_select": True}]

    bad = _FakeFinalizeDialogue(
        {
            "episode_id": "ep-1",
            "succeeded_jobs": 1,
            "finalized": [],
            "auto_selected": [],
            "failures": [{"job_id": "j9", "code": "TTS_MEDIA_UNVERIFIED", "message": "音频未验证"}],
        }
    )
    report = _run_tts_finalize_action(database, workspace, project_id, episode_id, bad, {"auto_select_videos": True})[2]
    assert report["status"] == "FAIL"
    assert report["machine_check"]["code"] == "TTS_FINALIZE_FAILED"

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from local_drama.application.episode_production_runs import EpisodeProductionRunService
from local_drama.application.jobs import JobService
from local_drama.application.projects import ProjectService


def _episode(workspace, database, code: str) -> tuple[dict, dict]:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code, title=code, episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    return project, episode


def _run(workspace, database, code: str, *, current_fingerprint: bool) -> tuple[EpisodeProductionRunService, dict, dict]:
    project, episode = _episode(workspace, database, code)
    service = EpisodeProductionRunService(database, workspace)
    actual = service.preflight(str(episode["id"]), tts_enabled=False, min_free_disk_bytes=1)
    fingerprint = str(actual["input_fingerprint"]) if current_fingerprint else "0" * 64
    preflight = {
        "input_fingerprint": fingerprint, "tts_enabled": False,
        "checks": [{"evidence": {}} for _ in range(6)] + [{"evidence": {"required_free_bytes": 1_000_000}}],
    }
    episode_context = {**episode, "project_id": project["id"]}
    workflow = service._workflow_for_snapshot(episode_context, preflight, actor="test")
    run = service.automation.start_run(str(workflow["id"]), plan_hash=str(workflow["plan_hash"]), idempotency_key=f"run-{code}")
    return service, run, episode


def _complete_with_report(workspace, database, run: dict, *, status: str = "PASS") -> str:
    job_id = str(run["tasks"][-1]["job_id"])
    jobs = JobService(database, workspace)
    claim = jobs.claim("crashed-after-completion", ["CPU"], lease_seconds=60)
    assert claim is not None and claim["job"]["id"] == job_id
    path = workspace.work_root / "jobs" / job_id / "report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"machine_check": {"status": status, "ok": status == "PASS"}}), encoding="utf-8")
    jobs.register_artifact(str(claim["attempt"]["id"]), "AUTOMATION_TASK_REPORT", path.relative_to(workspace.work_root).as_posix())
    jobs.complete(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "crashed-after-completion", success=True)
    return job_id


def test_recover_requeues_expired_worker_lease_without_replacing_run_or_job(workspace, database) -> None:
    service, run, _episode_row = _run(workspace, database, "episode_recover_lease", current_fingerprint=False)
    run_id, job_id = str(run["id"]), str(run["tasks"][0]["job_id"])
    jobs = JobService(database, workspace)
    claim = jobs.claim("worker-that-crashed", ["CPU"], lease_seconds=60)
    assert claim is not None and claim["job"]["id"] == job_id
    expired = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    with database.transaction() as connection:
        connection.execute("UPDATE job_attempts SET lease_expires_at=? WHERE id=?", (expired, claim["attempt"]["id"]))

    recovered = service.recover(run_id)

    assert recovered["run"]["id"] == run_id
    assert recovered["recovery"]["lease_reconcile"]["reconciled"] == 1
    assert recovered["recovery"]["requeued_job_ids"] == [job_id]
    assert JobService(database, workspace).get_job(job_id)["state"] == "QUEUED"
    assert recovered["recovery"]["refreshed_task_ids"] == [run["tasks"][0]["id"]]


def test_recover_advances_completed_report_once_and_unchanged_success_is_skipped(workspace, database) -> None:
    service, run, _episode_row = _run(workspace, database, "episode_recover_advance", current_fingerprint=True)
    job_id = _complete_with_report(workspace, database, run)

    first = service.recover(str(run["id"]))
    assert first["recovery"]["advanced_completed_job"] is True
    assert first["recovery"]["skipped_task_ids"] == [run["tasks"][0]["id"]]
    shot_image_stage = next(stage for stage in first["run"]["stages"] if stage["code"] == "SHOT_IMAGE")
    assert len(shot_image_stage["jobs"]) == 1  # keyframe task retained as evidence
    automation_after_first = service.automation.get_run(str(run["id"]))
    assert automation_after_first["task_count"] == 2
    assert automation_after_first["tasks"][0]["job_id"] == job_id

    second = service.recover(str(run["id"]))
    assert second["recovery"]["advanced_completed_job"] is False
    assert service.automation.get_run(str(run["id"]))["task_count"] == 2


def test_watchdog_advances_stale_run_after_worker_crashes_post_completion(workspace, database) -> None:
    service, run, _episode_row = _run(workspace, database, "episode_watchdog_advance", current_fingerprint=True)
    completed_job_id = _complete_with_report(workspace, database, run)
    stale = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    with database.transaction() as connection:
        connection.execute(
            "UPDATE automation_workflow_runs SET updated_at=? WHERE id=?",
            (stale, run["id"]),
        )

    result = service.watchdog(stale_seconds=30)

    assert result["recovered_run_ids"] == [run["id"]]
    recovered = service.automation.get_run(str(run["id"]))
    assert recovered["task_count"] == 2
    assert recovered["tasks"][0]["job_id"] == completed_job_id


def test_run_projects_eight_creator_stages_and_all_background_stage_codes(workspace, database) -> None:
    service, run, _episode_row = _run(workspace, database, "ep8stage", current_fingerprint=True)

    view = service.get(str(run["id"]), include_jobs=True)
    assert [stage["code"] for stage in view["stages"]] == [
        "STORY_ANALYSIS", "ASSET_EXTRACTION", "ASSET_COMPLETION", "SHOT_PLANNING",
        "SHOT_IMAGE", "VIDEO", "AUDIO_SUBTITLE", "COMPOSE_QC",
    ]
    mapped = {code for stage in view["stages"] for code in stage["background_stages"]}
    assert mapped == {
        "STORY_READY", "ASSET_READY", "SHOT_PLAN_READY", "KEYFRAME_GENERATION",
        "VIDEO_GENERATION", "QC", "AUDIO", "TIMELINE", "EPISODE_COMPOSE",
        "HUMAN_REVIEW", "DELIVERY_READY",
    }
    assert next(stage for stage in view["stages"] if stage["code"] == "SHOT_IMAGE")["jobs"]
    story = next(stage for stage in view["stages"] if stage["code"] == "STORY_ANALYSIS")
    assert story["status"] == "PENDING"
    assert story["remaining_count"] == 1
    assert story["needs_human_decision"] == 0
    assert story["estimate_status"] == "NOT_AVAILABLE"
    assert story["estimated_remaining_seconds"] is None

    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO audit_events
            (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
            VALUES ('test','writer','SCRIPT_BREAKDOWN_APPLIED','script_breakdown_draft','draft-8','applied',?)""",
            (json.dumps({"episode_id": str(_episode_row["id"])}),),
        )
    applied_view = service.get(str(run["id"]))
    assert next(stage for stage in applied_view["stages"] if stage["code"] == "STORY_ANALYSIS")["status"] == "COMPLETED"
    assert next(stage for stage in applied_view["stages"] if stage["code"] == "ASSET_EXTRACTION")["status"] == "COMPLETED"


def test_recover_rebuilds_stale_completed_cursor_without_mutating_terminal_job(workspace, database) -> None:
    service, run, _episode_row = _run(workspace, database, "episode_recover_stale", current_fingerprint=False)
    terminal_job_id = _complete_with_report(workspace, database, run)

    recovered = service.recover(str(run["id"]))

    assert recovered["recovery"]["advanced_completed_job"] is False
    assert recovered["recovery"]["stale_completed_task_ids"] == [run["tasks"][0]["id"]]
    assert recovered["recovery"]["rebuilt_task_ids"] == [run["tasks"][0]["id"]]
    replacement_job_id = recovered["recovery"]["rebuild_job_ids"][0]
    assert replacement_job_id != terminal_job_id
    jobs = JobService(database, workspace)
    assert jobs.get_job(terminal_job_id)["state"] == "SUCCEEDED"
    assert jobs.get_job(replacement_job_id)["state"] == "QUEUED"
    assert service.automation.get_run(str(run["id"]))["tasks"][0]["job_id"] == replacement_job_id

    replay = service.recover(str(run["id"]))
    assert replay["recovery"]["rebuild_job_ids"] == []
    assert service.automation.get_run(str(run["id"]))["task_count"] == 1


def test_pause_completion_race_resumes_and_consumes_completion_without_duplicate_task(workspace, database) -> None:
    service, run, _episode_row = _run(workspace, database, "episode_recover_pause_race", current_fingerprint=True)
    _complete_with_report(workspace, database, run)
    paused = service.pause(str(run["id"]), reason="operator paused during completion")
    assert paused["status"] == "PAUSED_HITL"

    resumed = service.resume(str(run["id"]), note="resume after persisted completion")

    assert resumed["status"] == "RUNNING"
    automation = service.automation.get_run(str(run["id"]))
    assert automation["task_count"] == 2
    assert len({task["ordinal"] for task in automation["tasks"]}) == 2

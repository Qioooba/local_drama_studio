from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from scripts.production_session_soak import REAL_SOAK_SECONDS, run_soak

from local_drama.application.jobs import JobService
from local_drama.application.production_sessions import ProductionSessionService
from local_drama.application.projects import ProjectService


def test_soak_evidence_is_resumable_and_never_labels_short_run_as_real_24h(
    workspace, database, tmp_path
) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="session_soak",
        title="生产会话长跑证据",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=30_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    request = {
        "scope_type": "SINGLE_EPISODE",
        "episode_ids": [str(episode["id"])],
        "production_mode": "BALANCED",
        "checkpoint_policy": "ON_EXCEPTION",
        "tts_enabled": False,
        "max_parallel_episodes": 1,
        "min_free_disk_bytes": 1,
    }
    sessions = ProductionSessionService(database)
    plan = sessions.plan(project_id, request)
    session = sessions.create(
        project_id,
        {**request, "expected_plan_hash": plan["plan_hash"], "actor": "test"},
        idempotency_key="soak-session",
    )["session"]
    item = sessions.list_items(str(session["id"]), cursor=0, limit=10)["items"][0]
    job = JobService(database, workspace).create_job(
        project_id,
        "CPU_TEST",
        "EPISODE",
        str(episode["id"]),
        "CPU",
        {},
        "soak-linked-job",
        scope_episode_id=str(episode["id"]),
    )
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO production_session_job_links
               (id,session_id,session_item_id,job_id,stage_code,role,link_state,
                created_at,updated_at,created_by,revision,schema_version)
               VALUES ('soak-link',?,?,?,'PREPARATION','TEST','ACTIVE',
                       CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'production-session.v1')""",
            (session["id"], item["id"], job["id"]),
        )

    output = tmp_path / "production-session-soak.json"
    started = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)
    first = run_soak(
        settings=workspace,
        database=database,
        session_id=str(session["id"]),
        output=output,
        duration_seconds=0,
        sample_interval_seconds=1,
        max_samples=10,
        drive_reconcile=False,
        clock=lambda: started,
        sleeper=lambda _seconds: None,
    )
    assert first["status"] == "PASS_SHORT_REHEARSAL"
    assert first["real_24h"] is False
    assert first["restart_count"] == 0
    assert first["samples"][-1]["job_states"] == {"QUEUED": 1}
    assert first["database_integrity"] == "ok"
    assert first["artifact_verification"] == {"valid": True, "count": 0, "items": []}

    resumed_at = started + timedelta(minutes=5)
    second = run_soak(
        settings=workspace,
        database=database,
        session_id=str(session["id"]),
        output=output,
        duration_seconds=0,
        sample_interval_seconds=1,
        max_samples=10,
        drive_reconcile=False,
        clock=lambda: resumed_at,
        sleeper=lambda _seconds: None,
    )
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert second["restart_count"] == 1
    assert len(second["invocations"]) == 2
    assert second["elapsed_wall_clock_seconds"] == 300
    assert persisted["restart_count"] == 1
    assert persisted["real_24h"] is False


def test_soak_refuses_real_24h_pass_without_real_production_activity(
    workspace, database, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("scripts.production_session_soak.REAL_SOAK_SECONDS", 120)
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="empty_real_soak",
        title="空会话不能冒充长跑",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=30_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    episode = projects.list_episodes(
        str(projects.list_seasons(project_id)[0]["id"])
    )[0]
    request = {
        "scope_type": "SINGLE_EPISODE",
        "episode_ids": [str(episode["id"])],
        "production_mode": "BALANCED",
        "checkpoint_policy": "ON_EXCEPTION",
        "tts_enabled": False,
        "max_parallel_episodes": 1,
        "min_free_disk_bytes": 1,
    }
    sessions = ProductionSessionService(database)
    plan = sessions.plan(project_id, request)
    session = sessions.create(
        project_id,
        {**request, "expected_plan_hash": plan["plan_hash"], "actor": "test"},
        idempotency_key="empty-real-soak",
    )["session"]
    started = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)
    clock_values = iter(
        [
            started,
            started + timedelta(seconds=60),
            started + timedelta(seconds=120),
            started + timedelta(seconds=120),
        ]
    )

    evidence = run_soak(
        settings=workspace,
        database=database,
        session_id=str(session["id"]),
        output=tmp_path / "empty-real-soak.json",
        duration_seconds=120,
        sample_interval_seconds=60,
        max_samples=10,
        drive_reconcile=False,
        clock=lambda: next(clock_values),
        sleeper=lambda _seconds: None,
    )

    assert evidence["real_24h"] is True
    assert evidence["observed_duration_seconds"] == 120
    assert evidence["production_activity_valid"] is False
    assert evidence["status"] == "FAILED"
    assert evidence["artifact_verification"]["count"] == 0
    assert "不能作为真实生产长跑通过证据" in evidence["limitations"][0]


def test_soak_does_not_count_a_long_recorder_gap_as_observed_time(
    workspace, database, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("scripts.production_session_soak.REAL_SOAK_SECONDS", 120)
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="soak_gap",
        title="记录器断档",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=30_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    episode = projects.list_episodes(
        str(projects.list_seasons(project_id)[0]["id"])
    )[0]
    request = {
        "scope_type": "SINGLE_EPISODE",
        "episode_ids": [str(episode["id"])],
        "production_mode": "BALANCED",
        "checkpoint_policy": "ON_EXCEPTION",
        "tts_enabled": False,
        "max_parallel_episodes": 1,
        "min_free_disk_bytes": 1,
    }
    sessions = ProductionSessionService(database)
    plan = sessions.plan(project_id, request)
    session = sessions.create(
        project_id,
        {**request, "expected_plan_hash": plan["plan_hash"], "actor": "test"},
        idempotency_key="soak-gap",
    )["session"]
    started = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)
    clock_values = iter(
        [started, started + timedelta(hours=24), started + timedelta(hours=24)]
    )

    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    evidence = run_soak(
        settings=workspace,
        database=database,
        session_id=str(session["id"]),
        output=tmp_path / "soak-gap.json",
        duration_seconds=120,
        sample_interval_seconds=10,
        max_samples=10,
        drive_reconcile=False,
        clock=lambda: next(clock_values),
        sleeper=interrupt,
    )

    assert evidence["status"] == "IN_PROGRESS"
    assert evidence["elapsed_wall_clock_seconds"] == REAL_SOAK_SECONDS
    assert evidence["observed_duration_seconds"] == 20
    assert evidence["real_24h"] is False

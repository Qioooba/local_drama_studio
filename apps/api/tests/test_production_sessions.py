from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.jobs import JobService
from local_drama.application.production_sessions import ProductionSessionService
from local_drama.application.projects import ProjectService
from local_drama.application.story_assets import StoryAssetService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _project(workspace, database, *, code: str, episode_count: int = 3):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code,
        title="持久生产会话测试",
        episode_count=episode_count,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episodes = projects.list_episodes(str(season["id"]))
    return project, episodes


def _request(*, scope_type: str, episode_ids: list[str], production_mode: str = "BALANCED"):
    return {
        "scope_type": scope_type,
        "episode_ids": episode_ids,
        "production_mode": production_mode,
        "checkpoint_policy": "ON_EXCEPTION",
        "tts_enabled": True,
        "max_parallel_episodes": 1,
        "min_free_disk_bytes": 1,
    }


def test_whole_drama_plan_is_deterministic_and_has_no_two_episode_pilot_limit(
    workspace, database,
) -> None:
    project, episodes = _project(workspace, database, code="session_whole", episode_count=3)
    service = ProductionSessionService(database)
    episode_ids = [str(item["id"]) for item in reversed(episodes)]

    first = service.plan(
        str(project["id"]),
        _request(scope_type="WHOLE_DRAMA", episode_ids=episode_ids, production_mode="QUALITY"),
    )
    second = service.plan(
        str(project["id"]),
        _request(scope_type="WHOLE_DRAMA", episode_ids=episode_ids, production_mode="QUALITY"),
    )

    assert first == second
    assert first["episode_count"] == 3
    assert [item["code"] for item in first["episodes"]] == [
        "EPISODE_001",
        "EPISODE_002",
        "EPISODE_003",
    ]
    assert first["configuration"]["candidate_count_per_shot"] == 4
    assert all(item["readiness"] == "NEEDS_PREPARATION" for item in first["episodes"])
    assert all(item["asset_bound_shot_count"] == 0 for item in first["episodes"])


def test_plan_warns_about_unbound_shots_and_binding_changes_plan_hash(
    workspace, database,
) -> None:
    project, episodes = _project(workspace, database, code="session_asset_readiness", episode_count=1)
    shot = ProjectService(database, workspace.projects_root).create_shot(
        str(episodes[0]["id"]), "SHOT-001", 4_000
    )
    service = ProductionSessionService(database)
    command = _request(
        scope_type="SINGLE_EPISODE",
        episode_ids=[str(episodes[0]["id"])],
    )

    before = service.plan(str(project["id"]), command)

    assert before["episodes"][0]["readiness"] == "NEEDS_PREPARATION"
    assert before["episodes"][0]["asset_bound_shot_count"] == 0
    assert before["episodes"][0]["missing_asset_binding_count"] == 1
    assert before["warnings"] == [
        {
            "code": "EPISODE_ASSET_BINDINGS_MISSING",
            "message": "EPISODE_001 有 1 个镜头尚无有效关键资产绑定；系统会尝试使用拆解提案自动准备，没有提案时会停下等待补充",
            "episode_id": str(episodes[0]["id"]),
            "missing_shot_count": 1,
        }
    ]

    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(
        str(project["id"]),
        kind="PROP",
        code="PROP_CAR",
        name="跑车",
        description="计划阶段只验证绑定存在；媒体缺口由运行时资产准备处理。",
        canonical_media_version_id=None,
        extra=None,
    )
    assets.bind_asset_to_shot(str(shot["id"]), asset_id=str(asset["id"]), role_in_shot="main")

    after = service.plan(str(project["id"]), command)

    assert after["episodes"][0]["readiness"] == "READY"
    assert after["episodes"][0]["asset_bound_shot_count"] == 1
    assert after["episodes"][0]["missing_asset_binding_count"] == 0
    assert after["warnings"] == []
    assert after["plan_hash"] != before["plan_hash"]


def test_single_episode_plan_requires_exactly_one_project_episode(workspace, database) -> None:
    project, episodes = _project(workspace, database, code="session_single", episode_count=2)
    service = ProductionSessionService(database)

    with pytest.raises(DomainRuleError) as missing:
        service.plan(
            str(project["id"]),
            _request(scope_type="SINGLE_EPISODE", episode_ids=[]),
        )
    assert missing.value.code == "PRODUCTION_SESSION_SINGLE_EPISODE_REQUIRED"

    with pytest.raises(DomainRuleError) as too_many:
        service.plan(
            str(project["id"]),
            _request(
                scope_type="SINGLE_EPISODE",
                episode_ids=[str(item["id"]) for item in episodes],
            ),
        )
    assert too_many.value.code == "PRODUCTION_SESSION_SINGLE_EPISODE_REQUIRED"


def test_create_query_and_control_session_are_durable_and_idempotent(workspace, database) -> None:
    project, episodes = _project(workspace, database, code="session_control", episode_count=2)
    service = ProductionSessionService(database)
    command = _request(
        scope_type="WHOLE_DRAMA",
        episode_ids=[str(item["id"]) for item in episodes],
    )
    plan = service.plan(str(project["id"]), command)
    create_command = {**command, "expected_plan_hash": plan["plan_hash"], "actor": "test-user"}

    created = service.create(
        str(project["id"]),
        create_command,
        idempotency_key="create-session-control",
    )
    replay = service.create(
        str(project["id"]),
        create_command,
        idempotency_key="create-session-control",
    )
    session_id = created["session"]["id"]

    assert created["session"]["status"] == "READY"
    assert created["session"]["item_count"] == 2
    assert created["session"]["allowed_actions"] == ["START", "PAUSE", "CANCEL"]
    assert replay["session"]["id"] == session_id
    assert replay["idempotent_replay"] is True

    listed = service.list_sessions(str(project["id"]), cursor=0, limit=10)
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == session_id

    page = service.list_items(session_id, cursor=0, limit=1)
    assert page["total"] == 2
    assert len(page["items"]) == 1
    assert page["next_cursor"] == 1
    assert page["items"][0]["state"] == "PENDING"

    paused = service.control(
        session_id,
        "PAUSE",
        {"expected_revision": 1, "actor": "test-user"},
        idempotency_key="pause-session-control",
    )
    assert paused["session"]["status"] == "PAUSED"
    assert paused["session"]["revision"] == 2

    resumed = service.control(
        session_id,
        "RESUME",
        {"expected_revision": 2, "actor": "test-user"},
        idempotency_key="resume-session-control",
    )
    assert resumed["session"]["status"] == "RUNNING"
    assert resumed["session"]["revision"] == 3

    cancelled = service.control(
        session_id,
        "CANCEL",
        {"expected_revision": 3, "actor": "test-user"},
        idempotency_key="cancel-session-control",
    )
    assert cancelled["session"]["status"] == "CANCELLED"
    assert cancelled["session"]["counters"]["cancelled"] == 2
    assert {item["state"] for item in service.list_items(session_id, cursor=0, limit=10)["items"]} == {
        "CANCELLED"
    }


def test_create_rejects_stale_plan_and_same_key_with_different_payload(workspace, database) -> None:
    project, episodes = _project(workspace, database, code="session_stale", episode_count=1)
    service = ProductionSessionService(database)
    command = _request(
        scope_type="SINGLE_EPISODE",
        episode_ids=[str(episodes[0]["id"])],
    )
    plan = service.plan(str(project["id"]), command)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE episodes SET revision=revision+1 WHERE id=?",
            (str(episodes[0]["id"]),),
        )

    with pytest.raises(DomainRuleError) as stale:
        service.create(
            str(project["id"]),
            {**command, "expected_plan_hash": plan["plan_hash"]},
            idempotency_key="stale-session-plan",
        )
    assert stale.value.code == "PRODUCTION_SESSION_PLAN_STALE"

    refreshed = service.plan(str(project["id"]), command)
    service.create(
        str(project["id"]),
        {**command, "expected_plan_hash": refreshed["plan_hash"]},
        idempotency_key="same-session-key",
    )
    with pytest.raises(DomainRuleError) as mismatch:
        service.create(
            str(project["id"]),
            {
                **command,
                "production_mode": "QUALITY",
                "expected_plan_hash": refreshed["plan_hash"],
            },
            idempotency_key="same-session-key",
        )
    assert mismatch.value.code == "PRODUCTION_SESSION_IDEMPOTENCY_MISMATCH"


def test_create_rejects_a_second_active_project_session(workspace, database) -> None:
    project, episodes = _project(workspace, database, code="session_active_conflict", episode_count=2)
    service = ProductionSessionService(database)
    first_command = _request(
        scope_type="SINGLE_EPISODE",
        episode_ids=[str(episodes[0]["id"])],
    )
    first_plan = service.plan(str(project["id"]), first_command)
    first = service.create(
        str(project["id"]),
        {**first_command, "expected_plan_hash": first_plan["plan_hash"]},
        idempotency_key="active-conflict-first",
    )
    second_command = _request(
        scope_type="SINGLE_EPISODE",
        episode_ids=[str(episodes[1]["id"])],
    )
    second_plan = service.plan(str(project["id"]), second_command)

    with pytest.raises(DomainRuleError) as conflict:
        service.create(
            str(project["id"]),
            {**second_command, "expected_plan_hash": second_plan["plan_hash"]},
            idempotency_key="active-conflict-second",
        )

    assert conflict.value.code == "PRODUCTION_SESSION_ACTIVE_CONFLICT"
    assert conflict.value.details["existing_session_id"] == first["session"]["id"]


def test_production_session_v2_http_contract(workspace, database) -> None:
    project, episodes = _project(workspace, database, code="session_http", episode_count=1)
    app = create_app(workspace)
    payload = _request(
        scope_type="SINGLE_EPISODE",
        episode_ids=[str(episodes[0]["id"])],
    )

    with TestClient(app) as client:
        planned = client.post(
            f"/api/v2/projects/{project['id']}/production-sessions:plan",
            json=payload,
        )
        assert planned.status_code == 200, planned.text
        plan = planned.json()["plan"]
        created = client.post(
            f"/api/v2/projects/{project['id']}/production-sessions",
            headers={"Idempotency-Key": "session-http-create"},
            json={**payload, "expected_plan_hash": plan["plan_hash"], "actor": "http-test"},
        )
        assert created.status_code == 201, created.text
        session = created.json()["session"]
        conflict = client.post(
            f"/api/v2/projects/{project['id']}/production-sessions",
            headers={"Idempotency-Key": "session-http-active-conflict"},
            json={**payload, "expected_plan_hash": plan["plan_hash"], "actor": "http-test"},
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "PRODUCTION_SESSION_ACTIVE_CONFLICT"

        fetched = client.get(f"/api/v2/production-sessions/{session['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["session"]["id"] == session["id"]
        items = client.get(f"/api/v2/production-sessions/{session['id']}/items")
        assert items.status_code == 200
        assert items.json()["total"] == 1
        review = client.get(f"/api/v2/production-sessions/{session['id']}/review")
        assert review.status_code == 200, review.text
        assert review.json()["items"][0]["review_status"] == "GENERATING"
        assert review.json()["human_approval_written"] is False

        extended = client.post(
            f"/api/v2/production-sessions/{session['id']}:extend-budget",
            headers={"Idempotency-Key": "session-http-extend-budget"},
            json={
                "expected_revision": session["revision"],
                "max_duration_seconds": 48 * 60 * 60,
                "actor": "http-test",
            },
        )
        assert extended.status_code == 200, extended.text
        extension = extended.json()
        assert extension["outcome"] == "BUDGET_EXTENDED"
        assert extension["extended"] == {"max_duration_seconds": 48 * 60 * 60}
        assert extension["session"]["budget"]["limits"]["max_duration_seconds"] == 48 * 60 * 60
        assert extension["session"]["status"] == "READY"
        assert extension["rearmed_item_count"] == 0

        replay = client.post(
            f"/api/v2/production-sessions/{session['id']}:extend-budget",
            headers={"Idempotency-Key": "session-http-extend-budget"},
            json={
                "expected_revision": session["revision"],
                "max_duration_seconds": 48 * 60 * 60,
                "actor": "http-test",
            },
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotent_replay"] is True


def test_retry_item_rearms_a_durable_session_with_a_new_attempt(workspace, database) -> None:
    project, episodes = _project(workspace, database, code="session_retry", episode_count=1)
    service = ProductionSessionService(database)
    command = _request(
        scope_type="SINGLE_EPISODE",
        episode_ids=[str(episodes[0]["id"])],
    )
    plan = service.plan(str(project["id"]), command)
    session = service.create(
        str(project["id"]),
        {**command, "expected_plan_hash": plan["plan_hash"]},
        idempotency_key="create-session-retry",
    )["session"]
    with database.transaction() as connection:
        connection.execute(
            "UPDATE production_sessions SET status='WAITING_REVIEW',current_stage='TIMELINE_PREVIEW' WHERE id=?",
            (session["id"],),
        )
        connection.execute(
            """UPDATE production_session_items SET state='BLOCKED',current_stage='TIMELINE_PREVIEW',
               progress_json='{"session_run_attempt":1}',last_error_code='RENDER_FAILED',
               last_error_message='render failed' WHERE session_id=?""",
            (session["id"],),
        )
        item = connection.execute(
            "SELECT id,revision FROM production_session_items WHERE session_id=?",
            (session["id"],),
        ).fetchone()

    retried = service.retry_item(
        str(session["id"]),
        str(item["id"]),
        {
            "expected_session_revision": 1,
            "expected_item_revision": int(item["revision"]),
            "strategy": "RETRY_FAILED_STAGE",
            "actor": "retry-test",
        },
        idempotency_key="retry-session-item",
    )
    replay = service.retry_item(
        str(session["id"]),
        str(item["id"]),
        {
            "expected_session_revision": 1,
            "expected_item_revision": int(item["revision"]),
            "strategy": "RETRY_FAILED_STAGE",
            "actor": "retry-test",
        },
        idempotency_key="retry-session-item",
    )

    assert retried["session"]["status"] == "RUNNING"
    assert retried["item"]["state"] == "PENDING"
    assert replay["idempotent_replay"] is True
    with database.connect() as connection:
        stored = connection.execute(
            "SELECT progress_json,last_error_code FROM production_session_items WHERE id=?",
            (item["id"],),
        ).fetchone()
    assert '"session_run_attempt":2' in stored["progress_json"]
    assert '"requested_operation":"RECOMPOSE_ONLY"' in stored["progress_json"]
    assert stored["last_error_code"] is None


def test_session_pause_resume_and_cancel_propagate_to_workflow_jobs(workspace, database) -> None:
    project, episodes = _project(workspace, database, code="session_control_jobs", episode_count=1)
    service = ProductionSessionService(database)
    command = _request(
        scope_type="SINGLE_EPISODE", episode_ids=[str(episodes[0]["id"])]
    )
    plan = service.plan(str(project["id"]), command)
    session = service.create(
        str(project["id"]),
        {**command, "expected_plan_hash": plan["plan_hash"]},
        idempotency_key="create-session-control-jobs",
    )["session"]
    automation = AutomationWorkflowService(database)
    workflow = automation.create_workflow(
        project_id=str(project["id"]),
        code="session-control-workflow",
        title="session control",
        mode="ASSISTED",
        nodes=[{"id": "render", "type": "LOCAL_TASK"}],
        batch_items=[{"key": "episode", "payload": {"episode_id": episodes[0]["id"]}}],
        conditions=[],
        max_iterations=1,
        max_tasks=1,
        max_disk_bytes=1024,
        human_gate="NONE",
        repeat_batch=False,
    )
    workflow_plan = automation.plan_workflow(str(workflow["id"]))
    run = automation.start_run(
        str(workflow["id"]),
        plan_hash=str(workflow_plan["plan_hash"]),
        idempotency_key="session-control-run",
    )
    stepped = automation.step_run(str(run["id"]))
    job_id = str(stepped["tasks"][0]["job_id"])
    reused_job = JobService(database).create_job(
        str(project["id"]),
        "SCRIPT_BREAKDOWN",
        "EPISODE",
        str(episodes[0]["id"]),
        "CPU",
        {},
        "session-control-reused-preparation",
        scope_episode_id=str(episodes[0]["id"]),
    )
    with database.transaction() as connection:
        item = connection.execute(
            "SELECT id FROM production_session_items WHERE session_id=?", (session["id"],)
        ).fetchone()
        connection.execute(
            "UPDATE production_sessions SET status='RUNNING' WHERE id=?", (session["id"],)
        )
        connection.execute(
            """UPDATE production_session_items SET state='RUNNING',progress_json=? WHERE id=?""",
            (f'{{"episode_run_id":"{run["id"]}"}}', item["id"]),
        )
        connection.execute(
            """INSERT INTO production_session_job_links
               (id,session_id,session_item_id,job_id,stage_code,role,link_state,created_by)
               VALUES ('session-control-link',?,?,?,'VIDEO','EPISODE_WORKFLOW_TASK','ACTIVE','test')""",
            (session["id"], item["id"], job_id),
        )
        connection.execute(
            """INSERT INTO production_session_job_links
               (id,session_id,session_item_id,job_id,stage_code,role,link_state,created_by)
               VALUES ('session-reused-link',?,?,?,'PREPARATION','EPISODE_PREPARATION_REUSED','ACTIVE','test')""",
            (session["id"], item["id"], reused_job["id"]),
        )

    paused = service.control(
        str(session["id"]),
        "PAUSE",
        {"expected_revision": 1, "actor": "control-test"},
        idempotency_key="pause-control-jobs",
    )
    assert paused["session"]["status"] == "PAUSED"
    assert automation.get_run(str(run["id"]))["status"] == "PAUSED_HITL"
    assert JobService(database).get_job(job_id)["state"] == "NEEDS_ATTENTION"

    resumed = service.control(
        str(session["id"]),
        "RESUME",
        {"expected_revision": 2, "actor": "control-test"},
        idempotency_key="resume-control-jobs",
    )
    assert resumed["session"]["status"] == "RUNNING"
    assert automation.get_run(str(run["id"]))["status"] == "RUNNING"
    assert JobService(database).get_job(job_id)["state"] == "QUEUED"

    cancelled = service.control(
        str(session["id"]),
        "CANCEL",
        {"expected_revision": 3, "actor": "control-test"},
        idempotency_key="cancel-control-jobs",
    )
    assert cancelled["session"]["status"] == "CANCELLED"
    assert automation.get_run(str(run["id"]))["status"] == "CANCELLED"
    assert JobService(database).get_job(job_id)["state"] == "CANCELLED"
    assert JobService(database).get_job(str(reused_job["id"]))["state"] == "QUEUED"

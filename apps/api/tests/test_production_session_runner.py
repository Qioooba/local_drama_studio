from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.episode_worker_actions import EpisodeWorkerActionService
from local_drama.application.jobs import JobService
from local_drama.application.production_session_runner import ProductionSessionRunner, _stage_for_action
from local_drama.application.production_sessions import ProductionSessionService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError


def test_session_stage_projection_covers_audio_finalize_and_timeline_assembly() -> None:
    assert _stage_for_action("TTS_FINALIZE") == "AUDIO_SUBTITLE"
    assert _stage_for_action("TIMELINE_ASSEMBLY") == "TIMELINE_PREVIEW"


def _seconds_ago(seconds: int) -> str:
    """A real UTC timestamp ``seconds`` in the past, in the service's storage format."""

    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _create_session(workspace, database, *, code: str, episode_count: int, max_parallel: int = 1):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code,
        title="持续生产会话",
        episode_count=episode_count,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    episodes = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))
    service = ProductionSessionService(database)
    request = {
        "scope_type": "WHOLE_DRAMA",
        "episode_ids": [str(item["id"]) for item in episodes],
        "production_mode": "BALANCED",
        "checkpoint_policy": "ON_EXCEPTION",
        "tts_enabled": True,
        "max_parallel_episodes": max_parallel,
        "min_free_disk_bytes": 1,
    }
    plan = service.plan(str(project["id"]), request)
    created = service.create(
        str(project["id"]),
        {**request, "expected_plan_hash": plan["plan_hash"], "actor": "runner-test"},
        idempotency_key=f"create-{code}",
    )
    return project, episodes, created["session"]


def test_runner_keeps_capacity_window_full_and_stops_at_waiting_review(
    workspace, database, monkeypatch,
) -> None:
    _project, _episodes, session = _create_session(
        workspace,
        database,
        code="runner_window",
        episode_count=3,
        max_parallel=1,
    )
    runner = ProductionSessionRunner(database, workspace)
    run_statuses: dict[str, str] = {}
    run_count = 0

    def prepare(episode_id: str, *, idempotency_key: str):
        assert episode_id
        assert idempotency_key.startswith("production-session:")
        return {"status": "READY", "episode_id": episode_id, "shot_count": 2}

    def start_episode(episode_id: str, **_kwargs):
        nonlocal run_count
        run_count += 1
        run_id = f"episode-run-{run_count}"
        run_statuses[run_id] = "RUNNING"
        return {"id": run_id, "status": "RUNNING", "tasks": []}

    def get_run(run_id: str):
        return {"id": run_id, "status": run_statuses[run_id], "tasks": [], "pending_gate": {}}

    monkeypatch.setattr(runner.preparation, "prepare", prepare)
    monkeypatch.setattr(runner.episode_runs, "start", start_episode)
    monkeypatch.setattr(runner.automation, "get_run", get_run)

    untouched = runner.reconcile(str(session["id"]), actor="runner-test")
    assert untouched["session"]["status"] == "READY"
    assert run_count == 0

    started = runner.start(
        str(session["id"]),
        {"expected_revision": session["revision"], "actor": "runner-test"},
        idempotency_key="start-runner-window",
    )
    assert started["dispatched_count"] == 1
    assert started["session"]["status"] == "RUNNING"
    replay = runner.start(
        str(session["id"]),
        {"expected_revision": session["revision"], "actor": "runner-test"},
        idempotency_key="start-runner-window",
    )
    assert replay["idempotent_replay"] is True
    assert run_count == 1
    states = ProductionSessionService(database).list_items(str(session["id"]), cursor=0, limit=10)["items"]
    assert [item["state"] for item in states] == ["RUNNING", "PENDING", "PENDING"]

    run_statuses["episode-run-1"] = "SUCCEEDED"
    second = runner.reconcile(str(session["id"]), actor="runner-test")
    assert second["dispatched_count"] == 1
    states = ProductionSessionService(database).list_items(str(session["id"]), cursor=0, limit=10)["items"]
    assert [item["state"] for item in states] == ["WAITING", "RUNNING", "PENDING"]
    assert states[0]["current_stage"] == "WAITING_REVIEW"

    run_statuses["episode-run-2"] = "SUCCEEDED"
    runner.reconcile(str(session["id"]), actor="runner-test")
    run_statuses["episode-run-3"] = "SUCCEEDED"
    finished = runner.reconcile(str(session["id"]), actor="runner-test")

    assert finished["session"]["status"] == "WAITING_REVIEW"
    assert finished["session"]["counters"]["waiting"] == 3
    assert finished["session"]["counters"]["review_waiting"] == 3
    assert finished["session"]["counters"]["machine_waiting"] == 0
    assert finished["session"]["counters"]["gate_waiting"] == 0
    assert finished["session"]["counters"]["completed"] == 0
    assert run_count == 3


def test_runner_marks_exhausted_blockers_waiting_user_until_item_retry(
    workspace, database
) -> None:
    project, _episodes, session = _create_session(
        workspace,
        database,
        code="runner_waiting_user",
        episode_count=1,
    )
    with database.transaction() as connection:
        connection.execute(
            """UPDATE production_sessions SET status='RUNNING',revision=revision+1
               WHERE id=?""",
            (session["id"],),
        )
        connection.execute(
            """UPDATE production_session_items
               SET state='BLOCKED',current_stage='ASSETS',
                   last_error_code='ASSET_REVIEW_REQUIRED',
                   last_error_message='资产身份需要人工核对',revision=revision+1
               WHERE session_id=?""",
            (session["id"],),
        )

    runner = ProductionSessionRunner(database, workspace)
    waiting = runner.reconcile(str(session["id"]), actor="runner-test")["session"]
    assert waiting["status"] == "WAITING_USER"
    assert waiting["allowed_actions"] == ["CANCEL"]
    with pytest.raises(DomainRuleError) as resume_error:
        ProductionSessionService(database).control(
            str(session["id"]),
            "RESUME",
            {"expected_revision": waiting["revision"], "actor": "runner-test"},
            idempotency_key="resume-non-gated-waiting-user",
        )
    assert resume_error.value.code == "PRODUCTION_SESSION_STATE_INVALID"
    assert runner.reconcile_active(actor="runner-test")["inspected"] == 0
    listed = ProductionSessionService(database).list_sessions(
        str(project["id"]), cursor=0, limit=10, status="WAITING_USER"
    )
    assert [item["id"] for item in listed["items"]] == [session["id"]]

    item = ProductionSessionService(database).list_items(
        str(session["id"]), cursor=0, limit=10
    )["items"][0]
    retried = ProductionSessionService(database).retry_item(
        str(session["id"]),
        str(item["id"]),
        {
            "expected_session_revision": waiting["revision"],
            "expected_item_revision": item["revision"],
            "strategy": "FULL_EPISODE",
            "actor": "runner-test",
        },
        idempotency_key="retry-waiting-user",
    )
    assert retried["session"]["status"] == "RUNNING"
    assert retried["item"]["state"] == "PENDING"


def test_runner_stops_when_review_ready_episode_and_blocked_episode_are_mixed(
    workspace, database
) -> None:
    _project, _episodes, session = _create_session(
        workspace,
        database,
        code="runner_review_and_blocked",
        episode_count=2,
        max_parallel=2,
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE production_sessions SET status='RUNNING',revision=revision+1 WHERE id=?",
            (session["id"],),
        )
        items = connection.execute(
            "SELECT id,ordinal FROM production_session_items WHERE session_id=? ORDER BY ordinal",
            (session["id"],),
        ).fetchall()
        connection.execute(
            """UPDATE production_session_items
               SET state='WAITING',current_stage='WAITING_REVIEW',revision=revision+1
               WHERE id=?""",
            (items[0]["id"],),
        )
        connection.execute(
            """UPDATE production_session_items
               SET state='BLOCKED',current_stage='ASSETS',
                   last_error_code='ASSET_REVIEW_REQUIRED',
                   last_error_message='资产身份需要人工核对',revision=revision+1
               WHERE id=?""",
            (items[1]["id"],),
        )

    waiting = ProductionSessionRunner(database, workspace)._summarize(
        str(session["id"]), actor="runner-test"
    )

    assert waiting["status"] == "WAITING_USER"
    assert waiting["current_stage"] == "ASSETS"
    assert waiting["counters"]["waiting"] == 1
    assert waiting["counters"]["review_waiting"] == 1
    assert waiting["counters"]["machine_waiting"] == 0
    assert waiting["counters"]["blocked"] == 1
    assert waiting["allowed_actions"] == ["CANCEL"]
    assert ProductionSessionRunner(database, workspace).reconcile_active(actor="runner-test")["inspected"] == 0


def test_creator_checkpoint_surfaces_session_resume_without_internal_run_id(
    workspace, database
) -> None:
    project, _episodes, session = _create_session(
        workspace,
        database,
        code="runner_creator_checkpoint",
        episode_count=1,
    )
    automation = AutomationWorkflowService(database)
    workflow = automation.create_workflow(
        project_id=str(project["id"]),
        code="session-checkpoint",
        title="生产会话创作者门禁",
        mode="ASSISTED",
        nodes=[
            {
                "id": "render",
                "type": "LOCAL_TASK",
                "metadata": {"checkpoint_policy": "BEFORE_VIDEO"},
            }
        ],
        batch_items=[{"key": "episode", "payload": {"action": "VIDEO_GENERATION"}}],
        conditions=[
            {
                "field": "machine_check.status",
                "operator": "EQ",
                "value": "FAIL",
                "action": "PAUSE_HITL",
            }
        ],
        max_iterations=2,
        max_tasks=2,
        max_disk_bytes=1024,
        human_gate="NONE",
        repeat_batch=True,
    )
    plan = automation.plan_workflow(str(workflow["id"]))
    run = automation.start_run(
        str(workflow["id"]),
        plan_hash=str(plan["plan_hash"]),
        idempotency_key="session-checkpoint-run",
    )
    paused = automation.step_run(
        str(run["id"]),
        machine_context={"status": "PASS", "machine_check": {"status": "PASS"}},
        actor="runner-test",
    )
    assert paused["status"] == "PAUSED_HITL"
    assert paused["pending_gate"]["reason"] == "CONFIGURED_CREATOR_CHECKPOINT"
    with database.transaction() as connection:
        connection.execute(
            "UPDATE production_sessions SET status='RUNNING',revision=revision+1 WHERE id=?",
            (session["id"],),
        )
        connection.execute(
            """UPDATE production_session_items
               SET state='WAITING',current_stage='VIDEO',progress_json=?,revision=revision+1
               WHERE session_id=?""",
            (
                json.dumps(
                    {
                        "episode_run_id": run["id"],
                        "episode_run_status": "PAUSED_HITL",
                        "pending_gate": paused["pending_gate"],
                    }
                ),
                session["id"],
            ),
        )

    waiting = ProductionSessionRunner(database, workspace)._summarize(
        str(session["id"]),
        actor="runner-test",
    )
    assert waiting["status"] == "WAITING_USER"
    assert waiting["allowed_actions"] == ["RESUME", "CANCEL"]
    assert waiting["counters"]["gate_waiting"] == 1
    assert waiting["counters"]["review_waiting"] == 0

    resumed = ProductionSessionService(database).control(
        str(session["id"]),
        "RESUME",
        {"expected_revision": waiting["revision"], "actor": "runner-test"},
        idempotency_key="resume-session-checkpoint",
    )
    assert resumed["session"]["status"] == "RUNNING"
    assert automation.get_run(str(run["id"]))["status"] == "RUNNING"
    with database.connect() as connection:
        job_state = connection.execute(
            """SELECT j.state FROM automation_workflow_run_tasks t
               JOIN jobs j ON j.id=t.job_id WHERE t.run_id=?""",
            (run["id"],),
        ).fetchone()[0]
    assert job_state == "QUEUED"


def test_exception_gate_blocks_only_its_episode_and_frees_capacity_for_next(
    workspace, database, monkeypatch,
) -> None:
    _project, episodes, session = _create_session(
        workspace,
        database,
        code="runner_independent_exception",
        episode_count=2,
        max_parallel=1,
    )
    with database.transaction() as connection:
        items = connection.execute(
            "SELECT id,episode_id FROM production_session_items WHERE session_id=? ORDER BY ordinal",
            (session["id"],),
        ).fetchall()
        # A session that is genuinely running *now*: the duration budget is measured
        # from ``started_at`` (default limit 24h), so a hard-coded past timestamp
        # would make every item fail the budget gate before the exception gate under
        # test was ever reached.
        connection.execute(
            "UPDATE production_sessions SET status='RUNNING',started_at=?,revision=revision+1 WHERE id=?",
            (_seconds_ago(60), session["id"]),
        )
        connection.execute(
            """UPDATE production_session_items
               SET state='WAITING',current_stage='AUDIO_SUBTITLE',progress_json=?,revision=revision+1
               WHERE id=?""",
            (json.dumps({"episode_run_id": "run-audio-blocked"}), items[0]["id"]),
        )
    runner = ProductionSessionRunner(database, workspace)
    monkeypatch.setattr(
        runner.automation,
        "get_run",
        lambda _run_id: {
            "id": "run-audio-blocked",
            "status": "PAUSED_HITL",
            "pending_gate": {
                "reason": "MACHINE_CHECK_REQUIRES_HITL",
                "machine_status": "FAIL",
            },
            "tasks": [
                {
                    "item_key": "TTS_FINALIZE",
                    "item": {"payload": {"action": "TTS_FINALIZE"}},
                }
            ],
        },
    )
    monkeypatch.setattr(
        runner.preparation,
        "prepare",
        lambda episode_id, *, idempotency_key: {
            "status": "READY",
            "episode_id": episode_id,
            "shot_count": 1,
        },
    )
    dispatched: list[str] = []

    def start_next(episode_id: str, **_kwargs):
        dispatched.append(episode_id)
        return {"id": "run-next", "status": "RUNNING", "tasks": []}

    monkeypatch.setattr(runner.episode_runs, "start", start_next)

    result = runner.reconcile(str(session["id"]), actor="runner-test")

    assert dispatched == [str(episodes[1]["id"])]
    assert result["session"]["status"] == "RUNNING"
    with database.connect() as connection:
        rows = connection.execute(
            """SELECT state,current_stage,last_error_code FROM production_session_items
               WHERE session_id=? ORDER BY ordinal""",
            (session["id"],),
        ).fetchall()
    assert [dict(row) for row in rows] == [
        {
            "state": "BLOCKED",
            "current_stage": "AUDIO_SUBTITLE",
            "last_error_code": "PRODUCTION_SESSION_STAGE_REVIEW_REQUIRED",
        },
        {"state": "RUNNING", "current_stage": "PREPARATION", "last_error_code": None},
    ]


def test_runner_persists_preparation_job_lineage(workspace, database, monkeypatch) -> None:
    project, episodes, session = _create_session(
        workspace,
        database,
        code="runner_prepare",
        episode_count=1,
    )
    job = JobService(database).create_job(
        str(project["id"]),
        "SCRIPT_BREAKDOWN",
        "EPISODE",
        str(episodes[0]["id"]),
        "CPU",
        {},
        "runner-prepare-job",
        scope_episode_id=str(episodes[0]["id"]),
    )
    runner = ProductionSessionRunner(database, workspace)
    monkeypatch.setattr(
        runner.preparation,
        "prepare",
        lambda episode_id, *, idempotency_key: {
            "status": "QUEUED",
            "episode_id": episode_id,
            "job_id": str(job["id"]),
            "shot_count": 0,
        },
    )

    started = runner.start(
        str(session["id"]),
        {"expected_revision": session["revision"], "actor": "runner-test"},
        idempotency_key="start-runner-prepare",
    )

    assert started["waiting_count"] == 1
    item = ProductionSessionService(database).list_items(str(session["id"]), cursor=0, limit=10)["items"][0]
    assert item["state"] == "WAITING"
    assert item["current_stage"] == "PREPARATION"
    with database.connect() as connection:
        link = connection.execute(
            "SELECT job_id,stage_code,role FROM production_session_job_links WHERE session_id=?",
            (session["id"],),
        ).fetchone()
    assert dict(link) == {
        "job_id": job["id"],
        "stage_code": "PREPARATION",
        "role": "EPISODE_PREPARATION_OWNED",
    }


def test_runner_surfaces_failed_generation_dependency_instead_of_staying_running(
    workspace, database
) -> None:
    project, episodes, session = _create_session(
        workspace,
        database,
        code="runner_dependency_failure",
        episode_count=1,
    )
    automation = AutomationWorkflowService(database)
    workflow = automation.create_workflow(
        str(project["id"]),
        code="runner-dependency-failure",
        title="dependency failure",
        mode="BATCH_AUTOMATED",
        nodes=[{"id": "episode", "type": "EPISODE_PRODUCTION_TASK"}],
        batch_items=[
            {
                "key": "asset-completion",
                "payload": {
                    "action": "ASSET_COMPLETION",
                    "episode_id": str(episodes[0]["id"]),
                },
            }
        ],
        conditions=[],
        max_iterations=2,
        max_tasks=2,
        max_disk_bytes=1_000_000,
        human_gate="ON_CONDITION",
    )
    run = automation.start_run(
        str(workflow["id"]),
        plan_hash=str(workflow["plan_hash"]),
        idempotency_key="runner-dependency-failure-run",
    )
    task_job_id = str(run["tasks"][0]["job_id"])
    with database.transaction() as connection:
        connection.execute(
            """UPDATE jobs SET state='NEEDS_ATTENTION',
               last_error_code='JOB_DEPENDENCY_FAILED',
               last_error_detail_redacted='upstream failed' WHERE id=?""",
            (task_job_id,),
        )
        connection.execute(
            "UPDATE production_sessions SET status='RUNNING' WHERE id=?",
            (session["id"],),
        )
        connection.execute(
            """UPDATE production_session_items
               SET state='RUNNING',current_stage='ASSETS',shot_count=1,progress_json=?
               WHERE session_id=?""",
            (json.dumps({"episode_run_id": run["id"]}), session["id"]),
        )

    result = ProductionSessionRunner(database, workspace).reconcile(
        str(session["id"]), actor="runner-test"
    )
    item = ProductionSessionService(database).list_items(
        str(session["id"]), cursor=0, limit=10
    )["items"][0]
    assert result["blocked_count"] == 1
    assert item["state"] == "BLOCKED"
    assert item["current_stage"] == "ASSETS"
    assert item["last_error_code"] == "PRODUCTION_SESSION_DEPENDENCY_FAILED"


def test_runner_local_retry_requeues_failed_owned_identity_job(
    workspace, database
) -> None:
    project, episodes, session = _create_session(
        workspace,
        database,
        code="runner_retry_identity_dependency",
        episode_count=1,
    )
    item = ProductionSessionService(database).list_items(
        str(session["id"]), cursor=0, limit=10
    )["items"][0]
    job = JobService(database).create_job(
        str(project["id"]),
        "CPU_TEST",
        "STORY_ASSET",
        "asset-test",
        "CPU",
        {},
        "runner-retry-identity-job",
        scope_episode_id=str(episodes[0]["id"]),
    )
    with database.transaction() as connection:
        connection.execute(
            """UPDATE jobs SET state='FAILED',last_error_code='TEST_FAILURE',
               last_error_detail_redacted='test' WHERE id=?""",
            (job["id"],),
        )
        connection.execute(
            """INSERT INTO production_session_job_links
               (id,session_id,session_item_id,job_id,stage_code,role,link_state,
                created_at,updated_at,created_by,revision,schema_version)
               VALUES ('retry-link',?,?,?,'ASSETS','IDENTITY_HERO','ACTIVE',
                       CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'production-session.v1')""",
            (session["id"], item["id"], job["id"]),
        )
    progress = {
        "retry": {"strategy": "RETRY_FAILED_STAGE"},
        "session_run_attempt": 2,
    }
    updated = ProductionSessionRunner(database, workspace)._retry_machine_dependencies(
        {"id": item["id"]}, progress, actor="runner-test"
    )
    assert updated is not None
    assert updated["retried_machine_dependency_job_ids"] == [job["id"]]
    assert JobService(database).get_job(str(job["id"]))["state"] == "QUEUED"


def test_runner_resumes_reroll_as_recompose_only(workspace, database, monkeypatch) -> None:
    _project, episodes, session = _create_session(
        workspace,
        database,
        code="runner_reroll_recompose",
        episode_count=1,
    )
    runner = ProductionSessionRunner(database, workspace)
    captured: dict[str, object] = {}
    with database.transaction() as connection:
        connection.execute(
            "UPDATE production_sessions SET status='RUNNING',current_stage='TIMELINE_PREVIEW' WHERE id=?",
            (session["id"],),
        )
        connection.execute(
            """UPDATE production_session_items
               SET state='PENDING',current_stage='TIMELINE_PREVIEW',shot_count=1,progress_json=?
               WHERE session_id=?""",
            (
                json.dumps(
                    {"requested_operation": "RECOMPOSE_ONLY", "session_run_attempt": 2}
                ),
                session["id"],
            ),
        )

    monkeypatch.setattr(
        EpisodeWorkerActionService,
        "operation_impact",
        lambda self, episode_id, **kwargs: {
            "episode_id": episode_id,
            "plan_hash": "d" * 64,
            "operation": kwargs["operation"],
        },
    )

    def start_episode(episode_id: str, **kwargs):
        captured.update(kwargs)
        return {"id": "reroll-recompose-run", "status": "RUNNING", "tasks": []}

    monkeypatch.setattr(runner.episode_runs, "start", start_episode)
    result = runner.reconcile(str(session["id"]), actor="reroll-test")

    assert result["dispatched_count"] == 1
    assert captured["operation"] == "RECOMPOSE_ONLY"
    assert captured["expected_plan_hash"] == "d" * 64
    assert captured["idempotency_key"].endswith(":episode-run:v2")
    assert captured["production_session_id"] == session["id"]
    assert str(episodes[0]["id"])


def test_runner_waits_for_disk_reserve_and_recovers_without_manual_retry(
    workspace, database, monkeypatch,
) -> None:
    _project, _episodes, session = _create_session(
        workspace,
        database,
        code="runner_resource_wait",
        episode_count=1,
    )
    runner = ProductionSessionRunner(database, workspace)
    disk = {"free": 0}
    starts: list[str] = []
    monkeypatch.setattr(
        "local_drama.application.production_session_runner.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=100, used=100 - disk["free"], free=disk["free"]),
    )
    monkeypatch.setattr(
        runner.preparation,
        "prepare",
        lambda episode_id, *, idempotency_key: {
            "status": "READY",
            "episode_id": episode_id,
            "shot_count": 1,
        },
    )
    monkeypatch.setattr(
        runner.episode_runs,
        "start",
        lambda episode_id, **_kwargs: (
            starts.append(episode_id)
            or {"id": "resource-recovered-run", "status": "RUNNING", "tasks": []}
        ),
    )

    started = runner.start(
        str(session["id"]),
        {"expected_revision": session["revision"], "actor": "resource-test"},
        idempotency_key="start-resource-wait",
    )
    item = ProductionSessionService(database).list_items(
        str(session["id"]), cursor=0, limit=10
    )["items"][0]
    assert started["session"]["status"] == "RUNNING"
    assert item["state"] == "WAITING"
    assert item["current_stage"] == "RESOURCE_WAIT"
    assert starts == []

    disk["free"] = 10
    runner.reconcile(str(session["id"]), actor="resource-test")
    recovered = ProductionSessionService(database).list_items(
        str(session["id"]), cursor=0, limit=10
    )["items"][0]
    assert recovered["state"] == "RUNNING"
    assert starts == [str(recovered["episode_id"])]


def test_runner_restart_recovers_persisted_window_without_duplicate_dispatch(
    workspace, database, monkeypatch,
) -> None:
    _project, _episodes, session = _create_session(
        workspace,
        database,
        code="runner_restart_recovery",
        episode_count=2,
        max_parallel=1,
    )
    run_statuses: dict[str, str] = {}
    dispatched_episodes: list[str] = []

    def wire(runner: ProductionSessionRunner) -> None:
        monkeypatch.setattr(
            runner.preparation,
            "prepare",
            lambda episode_id, *, idempotency_key: {
                "status": "READY",
                "episode_id": episode_id,
                "shot_count": 1,
            },
        )

        def start_episode(episode_id: str, **_kwargs):
            dispatched_episodes.append(episode_id)
            run_id = f"restart-run-{len(dispatched_episodes)}"
            run_statuses[run_id] = "RUNNING"
            return {"id": run_id, "status": "RUNNING", "tasks": []}

        monkeypatch.setattr(runner.episode_runs, "start", start_episode)
        monkeypatch.setattr(
            runner.automation,
            "get_run",
            lambda run_id: {
                "id": run_id,
                "status": run_statuses[run_id],
                "tasks": [],
                "pending_gate": {},
            },
        )

    first_process = ProductionSessionRunner(database, workspace)
    wire(first_process)
    first_process.start(
        str(session["id"]),
        {"expected_revision": session["revision"], "actor": "restart-test"},
        idempotency_key="start-restart-recovery",
    )
    assert len(dispatched_episodes) == 1

    second_process = ProductionSessionRunner(database, workspace)
    wire(second_process)
    second_process.reconcile(str(session["id"]), actor="restart-test")
    assert len(dispatched_episodes) == 1

    run_statuses["restart-run-1"] = "SUCCEEDED"
    second_process.reconcile(str(session["id"]), actor="restart-test")
    assert len(dispatched_episodes) == 2

    third_process = ProductionSessionRunner(database, workspace)
    wire(third_process)
    third_process.reconcile(str(session["id"]), actor="restart-test")
    assert len(dispatched_episodes) == 2


def test_runner_recovers_after_real_child_process_exit_without_duplicate_dispatch(
    workspace, database
) -> None:
    project, episodes, session = _create_session(
        workspace,
        database,
        code="runner_process_exit_recovery",
        episode_count=1,
        max_parallel=1,
    )
    item = ProductionSessionService(database).list_items(
        str(session["id"]), cursor=0, limit=10
    )["items"][0]
    automation = AutomationWorkflowService(database)
    workflow = automation.create_workflow(
        str(project["id"]),
        code="runner-process-exit-recovery",
        title="process exit recovery",
        mode="BATCH_AUTOMATED",
        nodes=[{"id": "episode", "type": "EPISODE_PRODUCTION_TASK"}],
        batch_items=[
            {
                "key": "asset-completion",
                "payload": {
                    "action": "ASSET_COMPLETION",
                    "episode_id": str(episodes[0]["id"]),
                },
            }
        ],
        conditions=[],
        max_iterations=2,
        max_tasks=2,
        max_disk_bytes=1_000_000,
        human_gate="ON_CONDITION",
    )
    run = automation.start_run(
        str(workflow["id"]),
        plan_hash=str(workflow["plan_hash"]),
        idempotency_key="runner-process-exit-run",
    )
    progress = json.dumps({"episode_run_id": str(run["id"])})
    child_code = "\n".join(
        [
            "import os, sqlite3",
            f"connection = sqlite3.connect({str(database.path)!r})",
            "connection.execute('BEGIN IMMEDIATE')",
            (
                "connection.execute(\"UPDATE production_sessions SET status='RUNNING', "
                "current_stage='ASSETS' WHERE id=?\", "
                f"({str(session['id'])!r},))"
            ),
            (
                "connection.execute(\"UPDATE production_session_items SET state='RUNNING', "
                "current_stage='ASSETS', shot_count=1, progress_json=? WHERE id=?\", "
                f"({progress!r}, {str(item['id'])!r}))"
            ),
            "connection.commit()",
            "connection.close()",
            "os._exit(17)",
        ]
    )
    child = subprocess.run(
        [sys.executable, "-c", child_code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 17

    fresh_runner = ProductionSessionRunner(database, workspace)
    first = fresh_runner.reconcile(str(session["id"]), actor="process-restart-test")
    second = ProductionSessionRunner(database, workspace).reconcile(
        str(session["id"]), actor="process-restart-test"
    )
    recovered = ProductionSessionService(database).list_items(
        str(session["id"]), cursor=0, limit=10
    )["items"][0]
    with database.connect() as connection:
        run_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM automation_workflow_runs WHERE workflow_id=?",
                (workflow["id"],),
            ).fetchone()[0]
        )
        linked_jobs = int(
            connection.execute(
                """SELECT COUNT(*) FROM production_session_job_links
                   WHERE session_id=? AND link_state='ACTIVE'""",
                (session["id"],),
            ).fetchone()[0]
        )
    assert first["dispatched_count"] == 0
    assert second["dispatched_count"] == 0
    assert recovered["state"] == "RUNNING"
    assert recovered["progress"]["episode_run_id"] == run["id"]
    assert run_count == 1
    assert linked_jobs == 1

"""A ``RUNNING`` workflow run whose task Job ended without success must not hang.

Reproduced defect: run ``8694eb35`` (task ``1:explainer:RESEARCH_ACQUIRE``, Job
``939a0891``) stayed ``RUNNING`` after its Job was cancelled through the generic
job authority.  ``step_run`` only advances a task whose Job reached
``SUCCEEDED`` (it raises ``AUTOMATION_COMPLETED_JOB_NOT_SUCCEEDED`` otherwise),
so nothing in the product could ever move that run again and the UI reported a
dead run as 运行中 forever.

These tests pin the honest stall report, the explicit recovery (re-queue the
same Job while its automatic attempt budget remains; fail the task and the run
when it does not) and the HTTP surface that exposes it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.jobs import JobService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.main import create_app

TASK_KEY = "1:explainer:RESEARCH_ACQUIRE"
EXPLAINER_RUN_ID = "stall-explainer-run"


def _project(workspace, database) -> str:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="stall_recovery",
        title="停滞恢复",
        episode_count=1,
        aspect_ratio=None,
        fps_num=None,
        fps_den=None,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    return str(project["id"])


def _running_run(workspace, database) -> tuple[AutomationWorkflowService, str, str]:
    """A BATCH_AUTOMATED run primed with exactly one QUEUED task Job."""

    project_id = _project(workspace, database)
    service = AutomationWorkflowService(database)
    workflow = service.create_workflow(
        project_id,
        code="stall-recovery",
        title="停滞恢复",
        mode="BATCH_AUTOMATED",
        nodes=[{"id": "only", "type": "LOCAL_TASK"}],
        batch_items=[{"key": "explainer:RESEARCH_ACQUIRE", "payload": {"step_code": "RESEARCH_ACQUIRE"}}],
        conditions=[],
        max_iterations=2,
        max_tasks=2,
        max_disk_bytes=1_000,
        human_gate="NONE",
        repeat_batch=False,
    )
    run = service.start_run(
        str(workflow["id"]),
        plan_hash=str(workflow["plan_hash"]),
        idempotency_key="stall-recovery-run",
    )
    assert run["status"] == "RUNNING"
    assert run["tasks"][0]["job_state"] == "QUEUED"
    return service, str(run["id"]), str(run["tasks"][0]["job_id"])


def _spend_attempt_budget(database, job_id: str, attempts: int) -> None:
    """Record attempts so the Job's automatic budget is really exhausted."""

    with database.transaction() as connection:
        for attempt_no in range(1, attempts + 1):
            connection.execute(
                "INSERT INTO job_attempts (id, job_id, attempt_no, state) VALUES (?, ?, ?, 'FAILED')",
                (f"stall-attempt-{attempt_no}", job_id, attempt_no),
            )


def test_a_cancelled_task_job_is_reported_as_a_stall(workspace, database) -> None:
    service, run_id, job_id = _running_run(workspace, database)
    assert service.describe_stall(run_id)["stalled"] is False

    JobService(database).cancel(job_id)

    stall = service.describe_stall(run_id)
    assert stall["stalled"] is True
    assert stall["reason"] == "TASK_JOB_CANCELLED"
    assert stall["job_id"] == job_id
    assert stall["job_state"] == "CANCELLED"
    assert stall["task_key"] == TASK_KEY
    assert stall["next_step"] == "REQUEUE_TASK_JOB"
    assert stall["recoverable"] is True
    # The run view carries the same report, so a list of runs cannot disagree
    # with the single-run read.
    assert service.get_run(run_id)["stall"] == stall
    assert service.list_runs(str(service.get_run(run_id)["project_id"]))["items"][0]["stall"] == stall


@pytest.mark.parametrize("job_state", ["QUEUED", "CLAIMED", "RUNNING"])
def test_a_run_waiting_on_a_live_job_is_not_reported_as_stalled(workspace, database, job_state: str) -> None:
    service, run_id, job_id = _running_run(workspace, database)
    with database.transaction() as connection:
        connection.execute("UPDATE jobs SET state=? WHERE id=?", (job_state, job_id))

    stall = service.describe_stall(run_id)
    assert stall["stalled"] is False
    assert stall["reason"] is None
    assert stall["next_step"] is None
    assert stall["recoverable"] is False
    assert stall["job_state"] == job_state


def test_a_run_that_is_not_running_is_not_reported_as_stalled(workspace, database) -> None:
    service, run_id, _job_id = _running_run(workspace, database)
    service.cancel_run(run_id)

    stall = service.describe_stall(run_id)
    assert service.get_run(run_id)["status"] == "CANCELLED"
    assert stall["stalled"] is False
    assert stall["run_status"] == "CANCELLED"


def test_recover_run_requeues_the_same_cancelled_job(workspace, database) -> None:
    service, run_id, job_id = _running_run(workspace, database)
    JobService(database).cancel(job_id)

    recovered = service.recover_run(run_id, actor="test-operator")

    assert recovered["status"] == "RUNNING"
    assert recovered["recovery"]["outcome"] == "JOB_REQUEUED"
    assert recovered["recovery"]["previous_job_state"] == "CANCELLED"
    assert recovered["recovery"]["job_id"] == job_id
    assert recovered["stall"]["stalled"] is False
    # The same Job is claimable again, with the cooperative stop flag cleared in
    # the same write, so a worker can pick the run back up.
    with database.connect() as connection:
        job = connection.execute(
            "SELECT state, cancel_requested_at, next_run_at, finished_at FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
    assert str(job["state"]) == "QUEUED"
    assert job["cancel_requested_at"] is None
    assert job["next_run_at"] is not None
    assert job["finished_at"] is None
    with database.connect() as connection:
        event = connection.execute(
            "SELECT event_json, created_by FROM automation_workflow_run_events WHERE run_id=? AND event_type='RECOVERED'",
            (run_id,),
        ).fetchone()
    assert event is not None
    assert "CANCELLED" in str(event["event_json"])
    assert str(event["created_by"]) == "test-operator"

    # Recovery is not a magic "make it succeed": a second call is refused because
    # the re-queued Job means the run is no longer stalled.
    with pytest.raises(DomainRuleError) as error:
        service.recover_run(run_id, actor="test-operator")
    assert error.value.code == "AUTOMATION_RUN_NOT_STALLED"


def test_recover_run_fails_the_run_when_the_attempt_budget_is_spent(workspace, database) -> None:
    service, run_id, job_id = _running_run(workspace, database)
    JobService(database).cancel(job_id)
    _spend_attempt_budget(database, job_id, attempts=2)

    stall = service.describe_stall(run_id)
    assert stall["stalled"] is True
    assert stall["recoverable"] is False
    assert stall["next_step"] == "FAIL_RUN"
    assert stall["attempts_used"] == 2
    assert stall["max_attempts"] == 2

    failed = service.recover_run(run_id, actor="test-operator")

    assert failed["status"] == "FAILED"
    assert failed["completed_at"] is not None
    assert failed["recovery"]["outcome"] == "RUN_FAILED"
    assert failed["recovery"]["previous_job_state"] == "CANCELLED"
    assert failed["tasks"][0]["status"] == "FAILED"
    # The reason names the real Job state instead of inventing a step result.
    assert failed["machine_context"]["last_error"]["job_state"] == "CANCELLED"
    assert "CANCELLED" in failed["machine_context"]["last_error"]["detail"]
    with database.connect() as connection:
        job_state = connection.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
        event = connection.execute(
            "SELECT event_json FROM automation_workflow_run_events WHERE run_id=? AND event_type='FAILED'",
            (run_id,),
        ).fetchone()
    assert str(job_state["state"]) == "CANCELLED"
    assert event is not None and "CANCELLED" in str(event["event_json"])


def _seed_explainer_run(database, workflow_run_id: str) -> None:
    """One explainer run projection pointing at the real workflow run."""

    with database.connect() as connection:
        run = connection.execute("SELECT project_id FROM automation_workflow_runs WHERE id=?", (workflow_run_id,)).fetchone()
    project_id = str(run["project_id"])
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale, input_kind,
            duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode, research_mode,
            status, created_at, updated_at, created_by)
            VALUES ('stall-video', ?, '停滞', '主题', 'FACTUAL_EXPLAINER', 'zh-CN', 'TOPIC',
            'FIXED', 300, 5, 'AUTO_WITH_EXCEPTIONS', 'LOCAL_ONLY', 'OFFLINE_IMPORT', 'DRAFT',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (project_id,),
        )
        ExplainerRepository(connection).insert(
            "explainer_runs",
            {
                "id": EXPLAINER_RUN_ID,
                "project_id": project_id,
                "video_id": "stall-video",
                "status": "RUNNING",
                "plan_hash": "e" * 64,
                "automation_workflow_run_id": workflow_run_id,
            },
        )


def test_recover_route_returns_the_service_result(workspace, database) -> None:
    service, run_id, job_id = _running_run(workspace, database)
    JobService(database).cancel(job_id)
    _seed_explainer_run(database, run_id)

    app = create_app(workspace)
    with TestClient(app) as client:
        # The read surface must show the stall before anyone acts on it.
        stall = client.get(f"/api/v2/explainer-runs/{EXPLAINER_RUN_ID}")
        assert stall.status_code == 200, stall.text
        assert stall.json()["run"]["stall"]["reason"] == "TASK_JOB_CANCELLED"

        response = client.post(
            f"/api/v2/explainer-runs/{EXPLAINER_RUN_ID}:recover",
            json={"reason": "任务作业已被取消", "actor": "test-operator"},
            headers={"Idempotency-Key": "recover-1"},
        )
        assert response.status_code == 200, response.text
        body = response.json()

    # The route returns the workflow run view plus the service's recovery
    # receipt, unmodified.
    expected = service.get_run(run_id)
    expected["recovery"] = body["recovery"]
    assert body == expected
    assert body["recovery"]["outcome"] == "JOB_REQUEUED"
    assert body["recovery"]["previous_job_state"] == "CANCELLED"

    with TestClient(create_app(workspace)) as client:
        refused = client.post(
            f"/api/v2/explainer-runs/{EXPLAINER_RUN_ID}:recover",
            json={"reason": "再次恢复", "actor": "test-operator"},
        )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "AUTOMATION_RUN_NOT_STALLED"

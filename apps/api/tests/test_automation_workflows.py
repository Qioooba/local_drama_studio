from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.jobs import JobService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _project(workspace, database) -> str:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="automation_workflow",
        title="声明式自动化",
        episode_count=1,
        aspect_ratio=None,
        fps_num=None,
        fps_den=None,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    return str(project["id"])


def _definition(project_id: str) -> dict[str, object]:
    return {
        "project_id": project_id,
        "code": "bounded-loop",
        "title": "有限批次循环",
        "mode": "ASSISTED",
        "nodes": [{"id": "render", "type": "LOCAL_TASK", "requires_human_approval": True}],
        "batch_items": [{"key": "shot-a", "payload": {"shot_id": "a"}}, {"key": "shot-b", "payload": {"shot_id": "b"}}],
        "conditions": [{"field": "machine_check.status", "operator": "EQ", "value": "FAIL", "action": "PAUSE_HITL"}],
        "max_iterations": 4,
        "max_tasks": 4,
        "max_disk_bytes": 100,
        "human_gate": "ON_CONDITION",
        "repeat_batch": True,
    }


def test_bounded_loop_pauses_on_machine_failure_and_requires_human_approval(workspace, database) -> None:
    project_id = _project(workspace, database)
    service = AutomationWorkflowService(database)
    workflow = service.create_workflow(**_definition(project_id))
    plan = service.plan_workflow(str(workflow["id"]))
    run = service.start_run(str(workflow["id"]), plan_hash=str(plan["plan_hash"]), idempotency_key="run-1")
    assert run["status"] == "RUNNING"
    paused = service.step_run(str(run["id"]), machine_context={"status": "FAIL"}, ai_scores={"quality": 1.0}, produced_bytes=20)
    assert paused["status"] == "PAUSED_HITL"
    assert paused["human_approval_status"] == "PENDING"
    assert paused["ai_scores"] == {"quality": 1.0}
    assert paused["ai_scores_can_approve"] is False
    assert paused["pending_gate"]["ai_score_ignored"] is True
    with pytest.raises(DomainRuleError) as error:
        service.step_run(str(run["id"]), machine_context={"status": "PASS"}, ai_scores={"approval": True})
    assert error.value.code == "AUTOMATION_RUN_NOT_RUNNING"
    approved = service.resume_run(str(run["id"]), decision="HUMAN_APPROVED", note="人工确认机器失败项后继续")
    assert approved["status"] == "RUNNING"
    next_step = service.step_run(str(run["id"]), machine_context={"status": "PASS"}, produced_bytes=20)
    assert next_step["status"] == "PAUSED_HITL"  # node-level hard gate remains
    assert next_step["iteration_count"] == 2
    rejected = service.resume_run(str(run["id"]), decision="HUMAN_REJECTED", note="人工拒绝继续")
    assert rejected["status"] == "FAILED"
    assert rejected["human_approval_status"] == "REJECTED"
    assert rejected["iteration_count"] == 2
    assert rejected["disk_bytes"] == 40


def test_limits_and_declarative_validation(workspace, database) -> None:
    project_id = _project(workspace, database)
    service = AutomationWorkflowService(database)
    invalid = _definition(project_id)
    invalid["conditions"] = [{"field": "ai_score", "operator": "GTE", "value": 0.8, "action": "AUTO_APPROVE"}]
    with pytest.raises(DomainRuleError) as error:
        service.create_workflow(**invalid)
    assert error.value.code == "AUTOMATION_CONDITION_FIELD_INVALID"
    valid = _definition(project_id)
    valid["nodes"] = [{"id": "render", "type": "LOCAL_TASK"}]
    valid["human_gate"] = "NONE"
    valid["max_iterations"] = 2
    valid["max_tasks"] = 2
    valid["max_disk_bytes"] = 10
    valid["repeat_batch"] = True
    workflow = service.create_workflow(**valid)
    plan = service.plan_workflow(str(workflow["id"]))
    run = service.start_run(str(workflow["id"]), plan_hash=str(plan["plan_hash"]), idempotency_key="run-2")
    first = service.step_run(str(run["id"]), produced_bytes=6)
    assert first["status"] == "RUNNING"
    limited = service.step_run(str(run["id"]), produced_bytes=5)
    assert limited["status"] == "LIMIT_REACHED"
    assert limited["disk_bytes"] == 6
    iteration_limited = _definition(project_id)
    iteration_limited["code"] = "iteration-limited"
    iteration_limited["nodes"] = [{"id": "render", "type": "LOCAL_TASK"}]
    iteration_limited["conditions"] = []
    iteration_limited["human_gate"] = "NONE"
    iteration_limited["max_iterations"] = 1
    iteration_limited["max_tasks"] = 10
    iteration_limited["max_disk_bytes"] = 100
    iteration_limited["repeat_batch"] = True
    bounded_workflow = service.create_workflow(**iteration_limited)
    bounded_plan = service.plan_workflow(str(bounded_workflow["id"]))
    bounded_run = service.start_run(str(bounded_workflow["id"]), plan_hash=str(bounded_plan["plan_hash"]), idempotency_key="run-iteration")
    bounded_step = service.step_run(str(bounded_run["id"]))
    assert bounded_step["status"] == "LIMIT_REACHED"
    assert bounded_step["iteration_count"] == 1


def test_workflow_api_uses_same_service_boundary(workspace, database) -> None:
    project_id = _project(workspace, database)
    app = create_app(workspace)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/projects/{project_id}/automation-workflows", json={k: v for k, v in _definition(project_id).items() if k != "project_id"})
        assert response.status_code == 201, response.text
        workflow = response.json()["workflow"]
        plan = client.post(f"/api/v1/automation-workflows/{workflow['id']}:plan")
        assert plan.status_code == 200
        start = client.post(
            f"/api/v1/automation-workflows/{workflow['id']}/runs",
            headers={"Idempotency-Key": "api-run-1"},
            json={"plan_hash": plan.json()["plan"]["plan_hash"]},
        )
        assert start.status_code == 201
        run_id = start.json()["run"]["id"]
        paused = client.post(f"/api/v1/automation-runs/{run_id}:pause", json={"reason": "人工暂停检查"})
        assert paused.status_code == 200
        assert paused.json()["run"]["status"] == "PAUSED_HITL"
        resumed = client.post(f"/api/v1/automation-runs/{run_id}:resume", json={"decision": "HUMAN_APPROVED", "note": "人工确认"})
        assert resumed.status_code == 200
        assert resumed.json()["run"]["status"] == "RUNNING"


def test_each_automation_task_has_durable_job_lineage_and_bounded_dependency(workspace, database) -> None:
    project_id = _project(workspace, database)
    service = AutomationWorkflowService(database)
    definition = _definition(project_id)
    definition["nodes"] = [{"id": "render", "type": "LOCAL_TASK"}]
    definition["human_gate"] = "NONE"
    definition["max_iterations"] = 3
    definition["max_tasks"] = 3
    workflow = service.create_workflow(**definition)
    plan = service.plan_workflow(str(workflow["id"]))
    run = service.start_run(str(workflow["id"]), plan_hash=str(plan["plan_hash"]), idempotency_key="job-bridge-run")
    first = service.step_run(str(run["id"]), machine_context={"status": "PASS"})
    second = service.step_run(str(run["id"]), machine_context={"status": "PASS"})
    assert first["tasks"][0]["job_id"]
    assert first["tasks"][0]["job_state"] == "QUEUED"
    assert second["tasks"][1]["job_id"]
    jobs = JobService(database)
    first_job = jobs.get_job(first["tasks"][0]["job_id"])
    second_job = jobs.get_job(second["tasks"][1]["job_id"])
    assert first_job["subject_type"] == "AUTOMATION_WORKFLOW_TASK"
    assert first_job["input_snapshot"]["automation_run_id"] == run["id"]
    assert first_job["input_snapshot"]["local_only"] is True
    assert first_job["input_snapshot"]["network_contacted"] is False
    assert second_job["input_snapshot"]["automation_task_id"] == second["tasks"][1]["id"]
    with database.connect() as connection:
        dependency = connection.execute(
            "SELECT depends_on_job_id FROM job_dependencies WHERE job_id=?",
            (second_job["id"],),
        ).fetchone()
        event = connection.execute(
            "SELECT event_json FROM automation_workflow_run_events WHERE run_id=? AND event_type='STEP_COMPLETED' ORDER BY created_at DESC LIMIT 1",
            (run["id"],),
        ).fetchone()
    assert dependency is not None and dependency["depends_on_job_id"] == first_job["id"]
    assert second_job["id"] in str(event["event_json"])

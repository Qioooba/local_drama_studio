from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from local_drama.application.jobs import JobService
from local_drama.application.production_session_runner import ProductionSessionRunner
from local_drama.application.production_sessions import ProductionSessionService
from local_drama.domain.errors import DomainRuleError
from tests.test_production_sessions import _project, _request


def _session(workspace, database, *, code: str, episode_count: int = 1):
    project, episodes = _project(workspace, database, code=code, episode_count=episode_count)
    service = ProductionSessionService(database)
    command = _request(
        scope_type="WHOLE_DRAMA",
        episode_ids=[str(episode["id"]) for episode in episodes],
    )
    plan = service.plan(str(project["id"]), command)
    result = service.create(
        str(project["id"]),
        {**command, "expected_plan_hash": plan["plan_hash"]},
        idempotency_key=f"create-{code}",
    )
    return project, episodes, result["session"]


def test_duration_budget_blocks_new_dispatch_and_extension_rearms_items(workspace, database) -> None:
    _project_row, _episodes, session = _session(workspace, database, code="budget_duration", episode_count=2)
    started_at = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    configuration = {**session["configuration"], "max_duration_seconds": 60}
    with database.transaction() as connection:
        connection.execute(
            """UPDATE production_sessions
               SET status='RUNNING',started_at=?,configuration_json=?,revision=revision+1
               WHERE id=?""",
            (started_at, json.dumps(configuration), session["id"]),
        )

    runner = ProductionSessionRunner(database, workspace)
    exhausted = runner.reconcile(str(session["id"]), actor="budget-test")["session"]
    items = ProductionSessionService(database).list_items(str(session["id"]), cursor=0, limit=10)["items"]
    assert exhausted["status"] == "WAITING_USER"
    assert exhausted["budget"]["hard_blockers"][0]["code"] == ("PRODUCTION_SESSION_DURATION_BUDGET_EXHAUSTED")
    assert {item["state"] for item in items} == {"BLOCKED"}
    assert {item["current_stage"] for item in items} == {"BUDGET_WAIT"}

    with pytest.raises(DomainRuleError) as retry_error:
        ProductionSessionService(database).retry_item(
            str(session["id"]),
            str(items[0]["id"]),
            {
                "expected_session_revision": exhausted["revision"],
                "expected_item_revision": items[0]["revision"],
                "strategy": "FULL_EPISODE",
                "actor": "budget-test",
            },
            idempotency_key="retry-without-extending-budget",
        )
    assert retry_error.value.code == "PRODUCTION_SESSION_RETRY_PREREQUISITE_REQUIRED"
    assert retry_error.value.details["prerequisites"][0]["action"] == "EXTEND_BUDGET"

    extended = ProductionSessionService(database).extend_budget(
        str(session["id"]),
        {
            "expected_revision": exhausted["revision"],
            "max_duration_seconds": 600,
            "actor": "budget-test",
        },
        idempotency_key="extend-duration-budget",
    )
    replay = ProductionSessionService(database).extend_budget(
        str(session["id"]),
        {
            "expected_revision": exhausted["revision"],
            "max_duration_seconds": 600,
            "actor": "budget-test",
        },
        idempotency_key="extend-duration-budget",
    )
    assert extended["rearmed_item_count"] == 2
    assert extended["session"]["status"] == "RUNNING"
    assert replay["idempotent_replay"] is True
    assert {item["state"] for item in ProductionSessionService(database).list_items(str(session["id"]), cursor=0, limit=10)["items"]} == {"PENDING"}

    with pytest.raises(DomainRuleError) as error:
        ProductionSessionService(database).extend_budget(
            str(session["id"]),
            {
                "expected_revision": extended["session"]["revision"],
                "max_duration_seconds": 600,
            },
            idempotency_key="do-not-reduce-budget",
        )
    assert error.value.code == "PRODUCTION_SESSION_BUDGET_NOT_INCREASED"


def test_gpu_queue_wait_recovers_without_manual_retry(workspace, database, monkeypatch) -> None:
    project, _episodes, session = _session(workspace, database, code="budget_gpu_wait")
    blocker = JobService(database, workspace).create_job(
        str(project["id"]),
        "CPU_TEST",
        "PROJECT",
        str(project["id"]),
        "GPU_H3",
        {},
        "occupy-gpu-window",
    )
    configuration = {**session["configuration"], "max_queued_gpu_jobs": 1}
    with database.transaction() as connection:
        connection.execute(
            "UPDATE production_sessions SET configuration_json=? WHERE id=?",
            (json.dumps(configuration), session["id"]),
        )
    runner = ProductionSessionRunner(database, workspace)
    started = runner.start(
        str(session["id"]),
        {"expected_revision": session["revision"], "actor": "budget-test"},
        idempotency_key="start-gpu-wait",
    )
    waiting_item = ProductionSessionService(database).list_items(str(session["id"]), cursor=0, limit=10)["items"][0]
    assert started["session"]["status"] == "RUNNING"
    assert waiting_item["state"] == "WAITING"
    assert waiting_item["current_stage"] == "RESOURCE_WAIT"
    assert waiting_item["progress"]["resource_wait"]["code"] == ("PRODUCTION_SESSION_GPU_QUEUE_WAIT")

    dispatched: list[str] = []
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
        lambda episode_id, **_kwargs: dispatched.append(episode_id) or {"id": "gpu-window-recovered", "status": "RUNNING", "tasks": []},
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET state='SUCCEEDED',finished_at=CURRENT_TIMESTAMP WHERE id=?",
            (blocker["id"],),
        )
    runner.reconcile(str(session["id"]), actor="budget-test")
    recovered = ProductionSessionService(database).list_items(str(session["id"]), cursor=0, limit=10)["items"][0]
    assert recovered["state"] == "RUNNING"
    assert dispatched == [recovered["episode_id"]]


def test_budget_counts_snapshot_owned_jobs_and_attempts(workspace, database) -> None:
    project, _episodes, session = _session(workspace, database, code="budget_usage")
    job = JobService(database, workspace).create_job(
        str(project["id"]),
        "CPU_TEST",
        "PROJECT",
        str(project["id"]),
        "CPU",
        {"production_session_id": session["id"]},
        "budget-owned-job",
    )
    claimed = JobService(database, workspace).claim("budget-worker", channels=["CPU"])
    assert claimed is not None
    assert claimed["job"]["id"] == job["id"]
    inspected = ProductionSessionService(database).get(str(session["id"]))
    assert inspected["budget"]["usage"]["new_jobs"] == 1
    assert inspected["budget"]["usage"]["attempts_total"] == 1


def test_duration_budget_starts_on_start_and_freezes_on_finish(workspace, database) -> None:
    _project_row, _episodes, session = _session(workspace, database, code="budget_clock")
    old_created_at = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    with database.transaction() as connection:
        connection.execute(
            "UPDATE production_sessions SET created_at=?,updated_at=? WHERE id=?",
            (old_created_at, old_created_at, session["id"]),
        )

    ready = ProductionSessionService(database).get(str(session["id"]))
    assert ready["status"] == "READY"
    assert ready["budget"]["usage"]["elapsed_seconds"] == 0
    assert ready["budget"]["hard_blockers"] == []

    started_at = datetime.now(UTC) - timedelta(hours=2)
    finished_at = started_at + timedelta(minutes=30)
    with database.transaction() as connection:
        connection.execute(
            """UPDATE production_sessions
               SET status='COMPLETED',started_at=?,finished_at=? WHERE id=?""",
            (started_at.isoformat(), finished_at.isoformat(), session["id"]),
        )

    finished = ProductionSessionService(database).get(str(session["id"]))
    assert finished["budget"]["usage"]["elapsed_seconds"] == 30 * 60

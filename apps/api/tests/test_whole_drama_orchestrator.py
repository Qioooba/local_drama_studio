from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.projects import ProjectService
from local_drama.application.whole_drama_orchestrator import WholeDramaOrchestratorService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


@pytest.mark.parametrize(
    ("statuses", "expected"),
    (
        ([], "EMPTY"),
        (["NOT_STARTED", "NOT_STARTED"], "NOT_STARTED"),
        (["SUCCEEDED", "SUCCEEDED"], "COMPLETED"),
        (["CANCELLED", "CANCELLED"], "CANCELLED"),
        (["PAUSED_HITL", "PAUSED_HITL"], "PAUSED_HITL"),
        (["FAILED", "FAILED"], "FAILED"),
        (["RUNNING", "FAILED"], "RUNNING"),
        (["SUCCEEDED", "NOT_STARTED"], "PARTIAL"),
    ),
)
def test_whole_drama_status_truth_table(statuses: list[str], expected: str) -> None:
    status, counts = WholeDramaOrchestratorService._summarize_run_states(statuses)

    assert status == expected
    assert sum(counts.values()) == len(statuses)


def test_inspect_associates_episode_from_run_task_not_first_workflow_node(
    workspace, database,
) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="drama_task_scope",
        title="Task scoped drama",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=4_000,
        allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(
        str(projects.list_seasons(str(project["id"]))[0]["id"])
    )[0]
    automation = AutomationWorkflowService(database)
    workflow = automation.create_workflow(
        str(project["id"]),
        code="unrelated-stable-code",
        title="Reordered nodes",
        mode="BATCH_AUTOMATED",
        nodes=[
            {"id": "first", "type": "NO_EPISODE_METADATA"},
            {
                "id": "episode-production",
                "type": "EPISODE_PRODUCTION_TASK",
                "metadata": {"episode_id": str(episode["id"])},
            },
        ],
        batch_items=[
            {
                "key": "episode-task",
                "payload": {"action": "VIDEO_GENERATION", "episode_id": str(episode["id"])},
            }
        ],
        conditions=[],
        max_iterations=2,
        max_tasks=2,
        max_disk_bytes=1_000_000,
        human_gate="NONE",
    )
    run = automation.start_run(
        str(workflow["id"]),
        plan_hash=str(workflow["plan_hash"]),
        idempotency_key="whole-drama-task-association",
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE automation_workflow_runs SET status='RUNNING' WHERE id=?", (run["id"],)
        )

    inspection = WholeDramaOrchestratorService(database, workspace).inspect(str(project["id"]))

    assert inspection["overall_status"] == "RUNNING"
    assert inspection["state_counts"] == {"RUNNING": 1}
    assert inspection["episodes"][0]["workflow_run_id"] == run["id"]


@pytest.mark.parametrize(
    ("successful_starts", "expected_status"),
    ((0, "NOT_STARTED"), (1, "PARTIALLY_DISPATCHED"), (3, "DISPATCHED")),
)
def test_whole_drama_run_reports_zero_partial_and_full_dispatch_truthfully(
    workspace, database, monkeypatch, successful_starts: int, expected_status: str,
) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=f"dispatch_{successful_starts}",
        title="Dispatch truth",
        episode_count=3,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=12_000,
        allow_unconfigured_capabilities=True,
    )
    service = WholeDramaOrchestratorService(database, workspace)
    calls = 0

    def start(_episode_id: str, **_kwargs):
        nonlocal calls
        calls += 1
        if calls > successful_starts:
            raise DomainRuleError("TEST_BLOCKED", "test blocker")
        return {"id": f"run-{calls}", "status": "RUNNING"}

    monkeypatch.setattr(service.run_service, "start", start)

    result = service.run(
        str(project["id"]),
        min_free_disk_bytes=1,
        idempotency_key=f"dispatch-{successful_starts}",
    )

    assert result["dispatch_status"] == expected_status
    assert result["dispatched_count"] == successful_starts
    assert result["blocked_count"] == 3 - successful_starts
    assert len(result["dispatched_runs"]) == 3


def test_whole_drama_pilot_scope_dispatches_only_two_selected_episodes(
    workspace, database, monkeypatch,
) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="selected_pilot", title="Selected pilot", episode_count=3,
        aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=12_000,
        allow_unconfigured_capabilities=True,
    )
    episodes = projects.list_episodes(
        str(projects.list_seasons(str(project["id"]))[0]["id"])
    )
    selected_ids = [str(episodes[0]["id"]), str(episodes[2]["id"])]
    service = WholeDramaOrchestratorService(database, workspace)
    started: list[str] = []

    def start(episode_id: str, **_kwargs):
        started.append(episode_id)
        if episode_id == selected_ids[0]:
            raise DomainRuleError("PILOT_REVIEW_REQUIRED", "first selected episode awaits review")
        return {"id": f"run-{episode_id}", "status": "RUNNING"}

    monkeypatch.setattr(service.run_service, "start", start)
    result = service.run(
        str(project["id"]),
        episode_ids=selected_ids,
        min_free_disk_bytes=1,
        idempotency_key="two-episode-pilot",
    )

    assert started == selected_ids
    assert result["total_episodes"] == 2
    assert result["dispatched_count"] == 1
    assert result["blocked_count"] == 1
    assert result["dispatch_status"] == "PARTIALLY_DISPATCHED"
    assert result["dispatched_runs"][0]["code_error"] == "PILOT_REVIEW_REQUIRED"
    assert result["dispatched_runs"][1]["status"] == "DISPATCHED"
    assert [item["episode_id"] for item in result["preparation"]["prepared_episodes"]] == selected_ids

    with pytest.raises(DomainRuleError) as too_large:
        service.run(
            str(project["id"]),
            episode_ids=[str(episode["id"]) for episode in episodes],
            min_free_disk_bytes=1,
            idempotency_key="three-episode-pilot",
        )
    assert too_large.value.code == "EPISODE_SCOPE_TOO_LARGE"

    with pytest.raises(DomainRuleError) as foreign:
        service.run(
            str(project["id"]),
            episode_ids=["not-in-project"],
            min_free_disk_bytes=1,
            idempotency_key="foreign-episode-pilot",
        )
    assert foreign.value.code == "EPISODE_SCOPE_INVALID"


def test_whole_drama_parent_command_replays_and_rejects_payload_mismatch(
    workspace, database, monkeypatch,
) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="whole_parent_identity", title="Whole parent identity", episode_count=1,
        aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=4_000,
        allow_unconfigured_capabilities=True,
    )
    service = WholeDramaOrchestratorService(database, workspace)
    calls = 0

    def run_once(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "project_id": str(project["id"]), "preparation": {"project_id": str(project["id"]), "prepared_episodes": [], "success": True},
            "dispatched_runs": [{"episode_id": "episode-1", "code": "EP01", "status": "DISPATCHED", "run_id": "run-1"}],
            "total_episodes": 1, "dispatched_count": 1, "blocked_count": 0,
            "dispatch_status": "DISPATCHED", "dispatch_reason": None,
        }

    monkeypatch.setattr(service, "_run_once", run_once)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda _index: service.run(str(project["id"]), min_free_disk_bytes=1, idempotency_key="parent-command"),
            range(2),
        ))

    assert calls == 1
    assert {result["dispatched_runs"][0]["run_id"] for result in results} == {"run-1"}
    assert sorted(result["idempotent_replay"] for result in results) == [False, True]
    with pytest.raises(DomainRuleError) as mismatch:
        service.run(
            str(project["id"]), production_mode="QUALITY", min_free_disk_bytes=1,
            idempotency_key="parent-command",
        )
    assert mismatch.value.code == "IDEMPOTENCY_PAYLOAD_MISMATCH"


def test_whole_drama_prepare_does_not_invent_missing_creative_decisions(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="drama_test",
        title="Whole Drama Test",
        episode_count=2,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=4000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episodes = projects.list_episodes(str(season["id"]))
    assert len(episodes) == 2

    # Add shots to episode 1 and episode 2
    projects.create_shot(str(episodes[0]["id"]), "E01_S01", 2000)
    projects.create_shot(str(episodes[0]["id"]), "E01_S02", 2000)
    projects.create_shot(str(episodes[1]["id"]), "E02_S01", 2000)

    # Publish dummy video profile
    with database.transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO execution_profiles (id, code, title) VALUES ('exec-video', 'video-prof', 'Video Profile')")
        conn.execute(
            """INSERT INTO execution_profile_versions
            (id, execution_profile_id, version_no, capability, model_bundle_json, input_contract_json,
             parameter_schema_json, status, capability_json, output_contract_json, resource_policy_json, workflow_version_id)
            VALUES ('prof-v1', 'exec-video', 1, 'VIDEO_I2V', '{}', '{}',
             '{"capabilities": {"camera": {"support": "PROMPT_FALLBACK", "prompt_fallback": true}}}',
             'PUBLISHED', '{}', '{}', '{}', 'wf-test')"""
        )
        conn.execute(
            """INSERT INTO project_profile_bindings
            (id, project_id, capability, execution_profile_version_id, status, created_at, updated_at, created_by, revision, schema_version)
            VALUES ('bind-1', ?, 'VIDEO_I2V', 'prof-v1', 'ACTIVE', datetime('now'), datetime('now'), 'test', 1, 'v2')""",
            (project["id"],),
        )

    service = WholeDramaOrchestratorService(database, workspace)
    inspection = service.inspect(str(project["id"]))

    assert inspection["project_code"] == "drama_test"
    assert inspection["total_episodes"] == 2
    assert len(inspection["episodes"]) == 2
    assert inspection["episodes"][0]["total_shots"] == 2
    assert inspection["episodes"][1]["total_shots"] == 1

    # Technical auto-healing must not invent action, continuity, or creative intent.
    prep = service.prepare_all_episodes(str(project["id"]))
    assert prep["success"] is False
    assert len(prep["prepared_episodes"]) == 2
    assert prep["prepared_episodes"][0]["status"] == "BLOCKED"
    assert prep["prepared_episodes"][0]["code_error"] == "EPISODE_SHOTS_READY_VALIDATION_FAILED"
    assert prep["prepared_episodes"][1]["status"] == "BLOCKED"
    assert prep["prepared_episodes"][1]["code_error"] == "EPISODE_SHOTS_READY_VALIDATION_FAILED"

    # No current revision is replaced and no blank shot becomes production-ready.
    inspection2 = service.inspect(str(project["id"]))
    assert inspection2["episodes"][0]["ready_shots"] == 0
    assert inspection2["episodes"][1]["ready_shots"] == 0

    # A repeated automation run remains blocked instead of manufacturing readiness.
    prep2 = service.prepare_all_episodes(str(project["id"]))
    assert prep2["success"] is False
    assert prep2["prepared_episodes"][0]["status"] == "BLOCKED"
    assert prep2["prepared_episodes"][1]["status"] == "BLOCKED"


def test_whole_drama_api_routes(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="drama_api",
        title="Whole Drama API Test",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=4000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episodes = projects.list_episodes(str(season["id"]))
    projects.create_shot(str(episodes[0]["id"]), "E01_S01", 2000)

    with TestClient(create_app(workspace)) as client:
        # GET status
        res = client.get(f"/api/v2/projects/{project['id']}/whole-drama:status")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["project_code"] == "drama_api"
        assert data["total_episodes"] == 1
        assert len(data["episodes"]) == 1

        # POST prepare (fails video profile validation as expected when profile not published)
        res_prep = client.post(f"/api/v2/projects/{project['id']}/whole-drama:prepare", json={"actor": "api-user"})
        assert res_prep.status_code == 200, res_prep.text
        prep_data = res_prep.json()
        assert prep_data["project_id"] == str(project["id"])

        # A production launch is a durable command and therefore cannot rely
        # on an optional, server-generated retry identity.
        missing_key = client.post(
            f"/api/v2/projects/{project['id']}/whole-drama:run",
            json={"min_free_disk_bytes": 1},
        )
        assert missing_key.status_code == 422

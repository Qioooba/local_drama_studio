from __future__ import annotations

import json
from local_drama.application.projects import ProjectService
from local_drama.application.whole_drama_orchestrator import WholeDramaOrchestratorService


def test_whole_drama_inspect_and_prepare(workspace, database) -> None:
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
    shot1_ep1 = projects.create_shot(str(episodes[0]["id"]), "E01_S01", 2000)
    shot2_ep1 = projects.create_shot(str(episodes[0]["id"]), "E01_S02", 2000)
    shot1_ep2 = projects.create_shot(str(episodes[1]["id"]), "E02_S01", 2000)

    # Publish dummy video profile
    with database.transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO execution_profiles (id, code, title) VALUES ('exec-video', 'video-prof', 'Video Profile')"
        )
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

    # Prepare all episodes (triggers auto-healing and marks shots ready)
    prep = service.prepare_all_episodes(str(project["id"]))
    assert prep["success"] is True
    assert len(prep["prepared_episodes"]) == 2
    assert prep["prepared_episodes"][0]["status"] == "CONFIRMED"
    assert prep["prepared_episodes"][0]["confirmed_shots"] == 2
    assert prep["prepared_episodes"][1]["status"] == "CONFIRMED"
    assert prep["prepared_episodes"][1]["confirmed_shots"] == 1

    # Inspect again to verify shots are ready
    inspection2 = service.inspect(str(project["id"]))
    assert inspection2["episodes"][0]["ready_shots"] == 2
    assert inspection2["episodes"][1]["ready_shots"] == 1

    # Second prepare should report ALREADY_READY idempotently
    prep2 = service.prepare_all_episodes(str(project["id"]))
    assert prep2["success"] is True
    assert prep2["prepared_episodes"][0]["status"] == "ALREADY_READY"
    assert prep2["prepared_episodes"][1]["status"] == "ALREADY_READY"


def test_whole_drama_api_routes(workspace, database) -> None:
    from fastapi.testclient import TestClient
    from local_drama.main import create_app

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


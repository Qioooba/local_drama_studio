from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.generation import GenerationService
from local_drama.application.jobs import JobService
from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def test_g9_lazy_canvas_layout_is_separate_from_dependencies_and_preflights(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="g9_canvas",
        title="G9 canvas",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    for number in range(1, 63):
        projects.create_shot(str(episode["id"]), f"S{number:03d}", 1000)

    with TestClient(create_app(workspace)) as client:
        first = client.get(f"/api/v1/canvas/EPISODE/{episode['id']}?limit=20")
        assert first.status_code == 200, first.text
        graph = first.json()["graph"]
        assert graph["page"] == {"cursor": 0, "limit": 20, "returned_shots": 20, "total_shots": 62, "next_cursor": 20}
        assert len(graph["nodes"]) == 100
        assert {"variant_lineage", "experiment_progress", "adjacent_constraints"}.issubset(graph["nodes"][0])
        assert graph["nodes"][0]["variant_lineage"] == []
        assert graph["nodes"][0]["experiment_progress"] == []
        assert graph["nodes"][0]["adjacent_constraints"] == []
        assert graph["invariants"]["layout_changes_business_dependencies"] is False
        original_edges = graph["edges"]
        node_id = graph["nodes"][0]["id"]

        saved = client.put(
            f"/api/v1/canvas/EPISODE/{episode['id']}/layout",
            json={"positions": {node_id: {"x": 321.5, "y": 42}}, "groups": [], "viewport": {"x": 0, "y": 0, "zoom": 1}},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["layout"]["revision"] == 1
        second = client.get(f"/api/v1/canvas/EPISODE/{episode['id']}?limit=20").json()["graph"]
        assert second["edges"] == original_edges
        assert second["nodes"][0]["position"] == {"x": 321.5, "y": 42.0}

        stale = client.put(
            f"/api/v1/canvas/EPISODE/{episode['id']}/layout",
            json={"expected_revision": 99, "positions": {node_id: {"x": 1, "y": 1}}},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
        unknown = client.put(
            f"/api/v1/canvas/EPISODE/{episode['id']}/layout",
            json={"expected_revision": 1, "positions": {"shot:unknown:direct": {"x": 1, "y": 1}}},
        )
        assert unknown.status_code == 422
        assert unknown.json()["error"]["code"] == "CANVAS_LAYOUT_NODE_UNKNOWN"

        plan = client.post(
            f"/api/v1/canvas/EPISODE/{episode['id']}/runs:preflight",
            json={"mode": "RANGE", "from_node_id": graph["nodes"][0]["id"], "to_node_id": graph["nodes"][4]["id"], "max_nodes": 10},
        )
        assert plan.status_code == 201, plan.text
        result = plan.json()["plan"]
        assert result["submitted"] is False
        assert result["estimate"]["node_count"] == 5
        assert result["estimate"]["max_parallel_gpu"] == 1
        assert result["status"] == "BLOCKED"

        next_page = client.get(f"/api/v1/canvas/EPISODE/{episode['id']}?cursor=20&limit=20").json()["graph"]
        assert next_page["page"]["returned_shots"] == 20
        assert next_page["nodes"][0]["shot_code"] == "S021"


def test_g9_canvas_excludes_archived_shots_from_counts_nodes_and_edges(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(code="g9_active_only", title="G9 active only", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True)
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    active = projects.create_shot(str(episode["id"]), "S_ACTIVE", 1000)
    archived = projects.create_shot(str(episode["id"]), "S_ARCHIVED", 1000)
    with database.transaction() as connection:
        connection.execute("UPDATE shots SET archived_at='2026-08-23T00:00:00Z' WHERE id=?", (str(archived["id"]),))
        connection.execute(
            """INSERT INTO shot_transition_constraints
            (id, from_shot_id, to_shot_id, constraint_type, enforcement, compatibility_status, note, created_at, updated_at, created_by, revision, schema_version)
            VALUES ('constraint-g9-archived', ?, ?, 'POSE_CONTINUITY', 'HARD', 'PASS', 'must not dangle', '2026-08-23T00:00:00Z', '2026-08-23T00:00:00Z', 'test', 1, 'v2')""",
            (str(active["id"]), str(archived["id"])),
        )

    from local_drama.application.canvas import ProductionCanvasService

    graph = ProductionCanvasService(database).graph("EPISODE", str(episode["id"]))
    assert graph["page"]["total_shots"] == 1
    assert graph["page"]["returned_shots"] == 1
    assert {node["shot_id"] for node in graph["nodes"]} == {str(active["id"])}
    assert all(str(archived["id"]) not in edge["source"] and str(archived["id"]) not in edge["target"] for edge in graph["edges"])


def test_g9_canvas_node_detail_aggregates_real_lineage_experiment_and_constraints(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(code="g9_detail", title="G9 detail", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True)
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    first = projects.create_shot(str(episode["id"]), "S001", 1000)
    second = projects.create_shot(str(episode["id"]), "S002", 1000)
    intent = GenerationService(database, workspace).create_intent(str(project["id"]), "SHOT", str(first["id"]), "I2V", "canvas detail")
    with database.transaction() as connection:
        now = "2026-08-14T00:00:00Z"
        connection.execute("""INSERT INTO generation_experiments
            (id, intent_id, title, axis_definitions_json, cell_count, max_parallel, resource_estimate_json, status, plan_hash, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 'canvas experiment', '{\"axes\":{\"seed\":[1,2]}}', 2, 1, '{}', 'CONFIRMED', 'hash', ?, ?, 'test', 1, 'v2')""", ("exp-g9-detail", str(intent["id"]), now, now))
        connection.execute("""INSERT INTO experiment_cells (id, experiment_id, cell_key, variant_id, job_id, status) VALUES ('cell-g9-1', 'exp-g9-detail', 'seed=1', NULL, NULL, 'SUCCEEDED')""")
        connection.execute("""INSERT INTO shot_transition_constraints
            (id, from_shot_id, to_shot_id, constraint_type, enforcement, compatibility_status, note, created_at, updated_at, created_by, revision, schema_version)
            VALUES ('constraint-g9-detail', ?, ?, 'POSE_CONTINUITY', 'HARD', 'PASS', 'canvas detail', ?, ?, 'test', 1, 'v2')""", (str(first["id"]), str(second["id"]), now, now))
    from local_drama.application.canvas import ProductionCanvasService

    graph = ProductionCanvasService(database).graph("EPISODE", str(episode["id"]))
    direct = next(node for node in graph["nodes"] if node["shot_id"] == str(first["id"]) and node["type"] == "DIRECT")
    assert direct["experiment_progress"] == [{"id": "exp-g9-detail", "title": "canvas experiment", "status": "CONFIRMED", "cell_count": 2, "expanded_count": 1, "succeeded_count": 1, "failed_count": 0}]
    assert direct["adjacent_constraints"][0]["constraint_type"] == "POSE_CONTINUITY"


def test_g9_canvas_node_exposes_derived_thumbnail_and_durable_job_logs(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(code="g9_thumbnail_logs", title="G9 thumbnail logs", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True)
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 1000)
    asset_id = "canvas-asset-g9"
    version_id = "canvas-version-g9"
    now = "2026-08-16T00:00:00Z"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO media_assets (id, project_id, owner_type, owner_id, purpose, media_kind, selected_version_id, version_counter, metadata_json, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 'SHOT', ?, 'GENERATED_OUTPUT', 'IMAGE', ?, 1, '{}', ?, ?, 'test', 1, 'v2')""",
            (asset_id, str(project["id"]), str(shot["id"]), version_id, now, now),
        )
        connection.execute(
            """INSERT INTO media_versions (id, media_asset_id, version_no, take_no, stage, rel_path, mime_type, byte_size, sha256, integrity_status, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, 1, 'KEYFRAME', 'media/canvas-preview.png', 'image/png', 0, ?, 'VERIFIED', ?, ?, 'test', 1, 'v2')""",
            (version_id, asset_id, "0" * 64, now, now),
        )
    JobService(database, workspace).create_job(str(project["id"]), "CANVAS_LOG", "SHOT", str(shot["id"]), "CPU", {"source": "canvas-test"}, "canvas-log-g9")
    from local_drama.application.canvas import ProductionCanvasService

    graph = ProductionCanvasService(database).graph("EPISODE", str(episode["id"]))
    keyframe = next(node for node in graph["nodes"] if node["shot_id"] == str(shot["id"]) and node["type"] == "KEYFRAME")
    assert keyframe["thumbnail_media_version_id"] == version_id
    assert keyframe["thumbnail_url"] == f"/api/v1/media-versions/{version_id}/thumbnail?size=small&frame=poster"
    assert keyframe["log_count"] >= 1
    assert keyframe["logs"][0]["type"] == "JOB_QUEUED"

from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.application.visual_labs import VisualLabService
from local_drama.application.workflow_runtime import WorkflowRuntimeService
from local_drama.application.workflows import WorkflowService
from local_drama.infrastructure.database.episode_production_repository import SqliteEpisodeProductionReadRepository
from local_drama.main import create_app


class _OfflineComfyNodes:
    base_url = "http://127.0.0.1:8188"

    def object_info(self) -> dict[str, object]:
        return {"SaveImage": {}}


def _project_episode(workspace, database, code: str):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=10_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    return projects, project, episode


def test_episode_production_v2_is_the_only_paginated_state_projection(workspace, database) -> None:
    projects, project, episode = _project_episode(workspace, database, "production_grid")
    for number in range(1, 4):
        projects.create_shot(str(episode["id"]), f"S{number:03d}", 1_000, "MEDIUM")

    grid = SqliteEpisodeProductionReadRepository(database).shot_facts(str(episode["id"]), cursor=0, limit=2, states=set())

    assert grid == {**grid, "cursor": 0, "limit": 2, "total": 3, "next_cursor": 2}
    assert len(grid["items"]) == 2
    assert [stage["stage_code"] for stage in grid["items"][0]["stages"]] == ["SHOT_PLANNING", "SHOT_IMAGE", "VIDEO", "AUDIO_SUBTITLE", "COMPOSE_QC"]
    assert grid["items"][0]["stages"][0]["state"] == "BLOCKED"

    with TestClient(create_app(workspace)) as client:
        response = client.get(f"/api/v2/episodes/{episode['id']}/production/shots?limit=2")
        legacy = client.get(f"/api/v1/episodes/{episode['id']}/production-grid?limit=2")
        retired = client.get(f"/api/v1/canvas/EPISODE/{episode['id']}")
    assert response.status_code == 200
    assert response.json()["items"][0]["shot_id"]
    assert legacy.status_code == 404
    assert retired.status_code == 404


def test_visual_lab_has_versioned_content_typed_edges_and_snapshots(workspace, database) -> None:
    projects, project, episode = _project_episode(workspace, database, "visual_lab")
    lab = VisualLabService(database, workspace)
    document = lab.create(str(project["id"]), "look-dev", "Look Development", str(episode["id"]))
    text = lab.add_node(str(document["id"]), {"node_kind": "TEXT_REF", "position_x": 10, "position_y": 20, "width": 260, "height": 180, "content": {"title": "情绪", "body": "雨夜"}, "expected_topology_revision": 1})
    generation = lab.add_node(str(document["id"]), {"node_kind": "GENERATION_INTENT", "position_x": 350, "position_y": 20, "width": 280, "height": 180, "content": {"title": "生成", "intent_id": "intent-placeholder", "variant_plan": {}}, "expected_topology_revision": 2})
    edge = lab.connect(str(document["id"]), {"source_node_id": text["id"], "source_port": "text", "target_node_id": generation["id"], "target_port": "prompt", "edge_kind": "GUIDES", "metadata": {}, "expected_topology_revision": 3})
    revised = lab.revise_node(str(text["id"]), {**text["content"], "body": "雨夜霓虹"}, "调整情绪", int(text["revision"]))
    snapshot = lab.snapshot(str(document["id"]))
    graph = lab.get(str(document["id"]))

    assert edge["source_port"] == "text" and edge["target_port"] == "prompt"
    assert revised["content_revision_no"] == 2
    assert snapshot["snapshot_no"] == 1
    assert graph["document"]["topology_revision"] == 4
    assert len(graph["nodes"]) == 2 and len(graph["edges"]) == 1
    deleted = lab.delete_node(str(generation["id"]), 4)
    after_delete = lab.get(str(document["id"]))
    assert deleted["deleted"] is True
    assert len(after_delete["nodes"]) == 1 and after_delete["edges"] == []


def test_infinite_canvas_persists_viewport_and_supports_atomic_duplicate_delete_restore(workspace, database) -> None:
    _, project, episode = _project_episode(workspace, database, "infinite_canvas")
    lab = VisualLabService(database, workspace)
    document = lab.create(str(project["id"]), "infinite", "Infinite Canvas", str(episode["id"]))
    note = lab.add_node(str(document["id"]), {"node_kind": "NOTE", "position_x": -2400, "position_y": 3600, "width": 280, "height": 180, "content": {"title": "远端便签", "body": "无限坐标"}, "expected_topology_revision": 1})
    frame = lab.add_node(str(document["id"]), {"node_kind": "FRAME", "position_x": -2480, "position_y": 3500, "width": 640, "height": 420, "content": {"title": "创作区域", "member_ids": [note["id"]]}, "expected_topology_revision": 2})
    viewport = lab.save_viewport(str(document["id"]), {"x": 1300, "y": -900, "zoom": 0.35})
    snapshot = lab.snapshot(str(document["id"]))

    duplicated = lab.duplicate_nodes(str(document["id"]), [str(note["id"]), str(frame["id"])], 48, 48, 3)
    assert len(duplicated["nodes"]) == 2
    assert duplicated["topology_revision"] == 4
    duplicated_note = next(node for node in duplicated["nodes"] if node["node_kind"] == "NOTE")
    lab.revise_node(
        str(duplicated_note["id"]),
        {**duplicated_note["content"], "body": "带多级历史的复制节点"},
        "验证级联删除历史链",
        int(duplicated_note["revision"]),
    )
    lab.delete_nodes(str(document["id"]), list(duplicated["id_mapping"].values()), 4)
    lab.delete_nodes(str(document["id"]), [str(note["id"])], 5)

    plan = lab.restore_preflight(str(snapshot["id"]))
    restored = lab.restore_snapshot(str(snapshot["id"]), str(plan["plan_hash"]))
    graph = restored["graph"]

    assert viewport == {"x": 1300.0, "y": -900.0, "zoom": 0.35}
    assert graph["document"]["viewport"] == viewport
    assert {node["id"] for node in graph["nodes"]} == {note["id"], frame["id"]}
    assert graph["document"]["topology_revision"] == 7
    assert lab.list_snapshots(str(document["id"]))[0]["id"] == snapshot["id"]


def test_runtime_environment_is_immutable_versioned_and_explicitly_published(workspace, database) -> None:
    service = WorkflowRuntimeService(database, workspace)
    created = service.create_environment("external-comfy", "External Comfy", {"mode": "EXTERNAL", "endpoint": "http://127.0.0.1:8188", "custom_nodes": [], "models": []})
    version = created["versions"][0]

    assert version["status"] == "DRAFT"
    published = service.publish_environment(str(version["id"]))
    assert published["status"] == "PUBLISHED"
    assert len(published["environment_fingerprint"]) == 64
    second = service.add_environment_version(str(created["environment"]["id"]), {"mode": "EXTERNAL", "endpoint": "http://127.0.0.1:8288", "custom_nodes": [], "models": []})
    assert second["version_no"] == 2 and second["status"] == "DRAFT"


def test_published_workflow_contract_binds_to_published_runtime(workspace, database) -> None:
    workflows = WorkflowService(database, workspace)
    workflow = workflows.register_package(
        "visual_lab_output",
        "Visual Lab output",
        {"1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "visual-lab"}}},
        {},
        {},
    )
    attestation = workflows.validate_against_comfy(str(workflow["id"]), _OfflineComfyNodes())  # type: ignore[arg-type]
    workflows.publish(str(workflow["id"]), str(attestation["validation_id"]))

    runtime = WorkflowRuntimeService(database, workspace)
    environment = runtime.create_environment(
        "contract-runtime",
        "Contract runtime",
        {"mode": "EXTERNAL", "endpoint": "http://127.0.0.1:8188", "custom_nodes": [], "models": []},
    )
    environment_version = runtime.publish_environment(str(environment["versions"][0]["id"]))
    contract = runtime.create_contract(
        str(workflow["id"]),
        "IMAGE_OUTPUT",
        {"inputs": {"output_prefix": {"type": "TEXT"}}, "outputs": {"image": {"type": "IMAGE"}}},
        {"output_prefix": {"node_id": "1", "input": "filename_prefix"}},
        [{"name": "编码输出", "node_ids": ["1"]}],
    )
    published_contract = runtime.publish_contract(str(contract["id"]))
    binding = runtime.bind(str(workflow["id"]), str(published_contract["id"]), str(environment_version["id"]))

    assert contract["validation"]["status"] == "PASS"
    assert published_contract["status"] == "PUBLISHED"
    assert binding["runtime_environment_version_id"] == environment_version["id"]

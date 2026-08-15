from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.read_models import ProductionReadModelService
from local_drama.main import create_app


def _fixture(workspace, database):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="continuity_context",
        title="Continuity context",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shots = [projects.create_shot(str(episode["id"]), f"S00{index}", 1_000) for index in range(1, 4)]
    projects.create_shot_revision(
        str(shots[0]["id"]),
        {"appearance": "short hair", "costume": "blue coat", "props": ["letter"], "lighting": "warm", "spatial_direction": "screen left", "continuity": "dry coat"},
        freeze=True,
    )
    projects.create_shot_revision(str(shots[1]["id"]), {"appearance": "short hair", "lighting": "warm", "continuity": "letter opened"}, freeze=True)
    projects.create_shot_revision(str(shots[2]["id"]), {"wardrobe": "blue coat", "screen_direction": "screen right"}, freeze=False)
    image = workspace.work_root / "continuity.png"
    image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
    media = MediaService(database, workspace).import_file(
        str(project["id"]), image, purpose="CONTINUITY_REFERENCE", owner_type="SHOT", owner_id=str(shots[1]["id"]), media_kind="IMAGE"
    )
    with database.transaction() as connection:
        connection.execute("UPDATE media_assets SET selected_version_id=? WHERE id=?", (media["media_version_id"], media["media_asset_id"]))
    return project, episode, shots, media


def test_continuity_context_projects_adjacent_revisions_and_safe_references(workspace, database) -> None:
    _, _, shots, media = _fixture(workspace, database)
    context = ProductionReadModelService(database).continuity_context(str(shots[1]["id"]))

    assert context["read_only"] is True
    assert context["runtime_contacted"] is False
    assert context["network_contacted"] is False
    assert context["mutated"] is False
    assert context["shots"]["previous"]["code"] == "S001"
    assert context["shots"]["current"]["code"] == "S002"
    assert context["shots"]["next"]["code"] == "S003"
    assert context["shots"]["previous"]["facets"]["costume"] == "blue coat"
    assert context["shots"]["next"]["facets"]["costume"] == "blue coat"
    assert context["shots"]["current"]["missing_facets"] == ["costume", "props", "spatial_direction"]
    reference = context["shots"]["current"]["references"][0]
    assert reference["media_version_id"] == media["media_version_id"]
    assert reference["selection_state"] == "SELECTED"
    assert "rel_path" not in reference


def test_continuity_context_api_handles_episode_edges_and_missing_shot(workspace, database) -> None:
    _, _, shots, _ = _fixture(workspace, database)
    with TestClient(create_app(workspace)) as client:
        response = client.get(f"/api/v1/shots/{shots[0]['id']}/continuity-context")
        missing = client.get("/api/v1/shots/00000000-0000-0000-0000-000000000000/continuity-context")

    assert response.status_code == 200
    assert response.json()["continuity"]["shots"]["previous"] is None
    assert response.json()["continuity"]["shots"]["next"]["code"] == "S002"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "SHOT_NOT_FOUND"

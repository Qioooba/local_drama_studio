"""Asset Bible commands + read model + API contract tests.

Covers handbook P2-03 (commands invariants), P2-04 (asset-bible aggregate),
and the new API facade.  Uses the real SQLite adapter through the service
layer, matching the existing story-asset test style.
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from local_drama.application.asset_multiview import AssetDetailService, AssetExpressionService
from local_drama.application.commands.asset_bible import AssetBibleCommandService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.queries.asset_bible import AssetBibleQueryService
from local_drama.application.story_assets import StoryAssetService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.asset_bible_repository import SqliteAssetBibleRepository
from local_drama.main import create_app

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


def _project(workspace, database, code: str):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 1_000)
    return projects, project, episode, shot


def _image(workspace, database, project_id: str, name: str = "ref.png") -> str:
    source = workspace.work_root / name
    source.write_bytes(PNG)
    return str(MediaService(database, workspace).import_file(project_id, source, media_kind="IMAGE")["media_version_id"])


def _service(database) -> tuple[AssetBibleCommandService, AssetBibleQueryService]:
    with database.connect() as connection:
        repository = SqliteAssetBibleRepository(connection)
    command = AssetBibleCommandService(repository)
    query = AssetBibleQueryService(repository)
    return command, query


def _write_service(database, fn):
    with database.transaction() as connection:
        repository = SqliteAssetBibleRepository(connection)
        return fn(AssetBibleCommandService(repository))


def _query_service(database, fn):
    with database.connect() as connection:
        repository = SqliteAssetBibleRepository(connection)
        return fn(AssetBibleQueryService(repository))


def test_state_crud_and_asset_scope(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_state_crud")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_A", "阿宁")

    created = _write_service(database, lambda s: s.create_state(project_id, str(asset["id"]), "INJURED", "受伤", "INJURY", "袖口破损"))
    assert created["code"] == "INJURED"
    assert created["state_kind"] == "INJURY"

    listed = _query_service(database, lambda s: s.list_states(str(asset["id"])))
    assert [item["code"] for item in listed] == ["INJURED"]

    updated = _write_service(database, lambda s: s.update_state(str(created["id"]), 1, label="受伤（轻）"))
    assert updated["label"] == "受伤（轻）"
    assert updated["revision"] == 2

    with pytest.raises(DomainRuleError) as conflict:
        _write_service(database, lambda s: s.update_state(str(created["id"]), 1, label="过期"))
    assert conflict.value.code == "STORY_ASSET_STATE_REVISION_CONFLICT"

    with pytest.raises(DomainRuleError) as wrong_kind:
        _write_service(database, lambda s: s.create_state(project_id, str(asset["id"]), "X1", "坏状态", "NOT_A_KIND"))
    assert wrong_kind.value.code == "STORY_ASSET_STATE_KIND_INVALID"


def test_reference_add_scope_and_hero_projection(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_ref_scope")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_H", "主角")
    media = _image(workspace, database, project_id, "hero.png")

    reference = _write_service(database, lambda s: s.add_reference(project_id, str(asset["id"]), media, "HERO"))
    assert reference["reference_kind"] == "HERO"
    # HERO projection updates canonical for compatibility with legacy code.
    fresh = assets.get_asset(str(asset["id"]))
    assert fresh["canonical_media_version_id"] == media
    assert fresh["revision"] == 2

    # Cross-project media rejected: asset belongs to `project`, media to `other_project`.
    _, other_project, _, _ = _project(workspace, database, "bible_ref_other")
    other_media = _image(workspace, database, str(other_project["id"]), "other-hero.png")
    with pytest.raises(DomainRuleError) as cross:
        _write_service(database, lambda s: s.add_reference(project_id, str(asset["id"]), other_media, "FRONT"))
    assert cross.value.code == "STORY_ASSET_MEDIA_SCOPE_INVALID"

    # Invalid kind rejected.
    with pytest.raises(DomainRuleError) as bad_kind:
        _write_service(database, lambda s: s.add_reference(project_id, str(asset["id"]), media, "NOT_A_KIND"))
    assert bad_kind.value.code == "STORY_ASSET_REFERENCE_KIND_INVALID"


def test_reference_archive_keeps_media_history(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_ref_archive")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_B", "角色乙")
    media = _image(workspace, database, project_id, "archive.png")

    reference = _write_service(database, lambda s: s.add_reference(project_id, str(asset["id"]), media, "FRONT"))
    archived = _write_service(database, lambda s: s.archive_reference(str(reference["id"]), 1))
    assert archived["status"] == "ARCHIVED"
    # Media version itself still exists (lineage preserved).
    with database.connect() as connection:
        assert connection.execute("SELECT 1 FROM media_versions WHERE id=?", (media,)).fetchone() is not None
    listed = _query_service(database, lambda s: s.list_references(str(asset["id"])))
    assert listed == []


def test_episode_and_shot_state_bindings(workspace, database) -> None:
    _, project, episode, shot = _project(workspace, database, "bible_bindings")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_C", "角色丙")
    state = _write_service(database, lambda s: s.create_state(project_id, str(asset["id"]), "INJURED", "受伤", "INJURY"))

    binding = _write_service(database, lambda s: s.set_episode_asset_state(episode_id=str(episode["id"]), story_asset_id=str(asset["id"]), asset_state_id=str(state["id"])))
    assert binding["asset_state_id"] == state["id"]

    resolved = _write_service(database, lambda s: s.episode_asset_state(str(episode["id"]), str(asset["id"])))
    assert resolved is not None
    assert resolved["state_label"] == "受伤"

    # Shot-level override requires the asset to be bound to the shot first.
    assets.bind_asset_to_shot(str(shot["id"]), str(asset["id"]), "main")
    shot_binding = _write_service(database, lambda s: s.set_shot_asset_state(shot_id=str(shot["id"]), asset_id=str(asset["id"]), asset_state_id=str(state["id"])))
    assert shot_binding["asset_state_id"] == state["id"]
    states = _write_service(database, lambda s: s.shot_asset_states(str(shot["id"])))
    assert states[0]["asset_state_id"] == state["id"]


def test_asset_bible_read_model_groups_references_by_state(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_read_model")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_D", "角色丁")
    hero = _image(workspace, database, project_id, "read-hero.png")
    front = _image(workspace, database, project_id, "read-front.png")

    _write_service(database, lambda s: s.add_reference(project_id, str(asset["id"]), hero, "HERO"))
    base_state = _write_service(database, lambda s: s.create_state(project_id, str(asset["id"]), "BASE", "基础造型", "BASE"))
    _write_service(database, lambda s: s.add_reference(project_id, str(asset["id"]), front, "FRONT", asset_state_id=str(base_state["id"])))

    bible = _query_service(database, lambda s: s.asset_bible(project_id))
    assert bible["asset_count"] == 1
    detail = bible["items"][0]
    assert detail["asset"]["name"] == "角色丁"
    assert detail["readiness"]["missing"] == ["LEFT", "RIGHT"]
    base_refs = [item for state in detail["states"] if state["code"] == "BASE" for item in state["references"]]
    assert len(base_refs) == 1
    assert base_refs[0]["reference_kind"] == "FRONT"


def test_asset_bible_api_full_flow(workspace, database) -> None:
    _, project, episode, shot = _project(workspace, database, "bible_api")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_API", "接口角色")
    media = _image(workspace, database, project_id, "api.png")

    with TestClient(create_app(workspace)) as client:
        state = client.post(
            f"/api/v1/story-assets/{asset['id']}/states",
            json={"code": "INJURED", "label": "受伤", "state_kind": "INJURY"},
        )
        reference = client.post(
            f"/api/v1/story-assets/{asset['id']}/references",
            json={"media_version_id": media, "reference_kind": "HERO"},
        )
        bible = client.get(f"/api/v1/projects/{project_id}/asset-bible")
        detail = client.get(f"/api/v1/story-assets/{asset['id']}/detail")
        refs = client.get(f"/api/v1/story-assets/{asset['id']}/references")
        episode_bind = client.post(
            f"/api/v1/episodes/{episode['id']}/asset-state-bindings",
            json={"story_asset_id": asset["id"], "asset_state_id": state.json()["state"]["id"]},
        )
        assets.bind_asset_to_shot(str(shot["id"]), str(asset["id"]), "main")
        shot_bind = client.post(
            f"/api/v1/shots/{shot['id']}/asset-state-bindings",
            json={"asset_id": asset["id"], "asset_state_id": state.json()["state"]["id"]},
        )
        archived = client.post(
            f"/api/v1/story-asset-references/{reference.json()['reference']['id']}:archive",
            json={"expected_revision": 1},
        )

    assert state.status_code == 201
    assert state.json()["state"]["state_kind"] == "INJURY"
    assert reference.status_code == 201
    assert reference.json()["reference"]["reference_kind"] == "HERO"
    assert bible.status_code == 200
    assert bible.json()["bible"]["asset_count"] == 1
    assert bible.json()["bible"]["items"][0]["asset"]["code"] == "CHAR_API"
    assert detail.status_code == 200
    assert detail.json()["asset_detail"]["readiness"]["level"] in {"BASIC", "READY"}
    assert refs.status_code == 200
    assert len(refs.json()["items"]) == 1
    assert episode_bind.status_code == 201
    assert shot_bind.status_code == 201
    assert archived.status_code == 201
    assert archived.json()["reference"]["status"] == "ARCHIVED"


def test_asset_bible_api_rejects_scope_violations(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_api_scope")
    project_id = str(project["id"])
    _, other_project, _, _ = _project(workspace, database, "bible_api_other")
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_S", "作用域角色")
    other_media = _image(workspace, database, str(other_project["id"]), "other.png")

    with TestClient(create_app(workspace)) as client:
        cross = client.post(
            f"/api/v1/story-assets/{asset['id']}/references",
            json={"media_version_id": other_media, "reference_kind": "FRONT"},
        )
        missing = client.post(
            f"/api/v1/story-assets/{asset['id']}/states",
            json={"code": "X", "label": "坏", "state_kind": "NOPE"},
        )
    assert cross.status_code == 422
    assert cross.json()["error"]["code"] == "STORY_ASSET_MEDIA_SCOPE_INVALID"
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "STORY_ASSET_STATE_KIND_INVALID"


def test_expression_preflight_uses_real_capability_and_never_queues_when_unavailable(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_expression_preflight")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_EXPR", "Expression Character")
    hero = _image(workspace, database, project_id, "expression-hero.png")
    _write_service(database, lambda service: service.add_reference(project_id, str(asset["id"]), hero, "HERO", is_locked=True))

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v1/story-assets/{asset['id']}/generate-expression:preflight",
            json={"consistency_strength": "HIGH", "background": "CLEAN"},
        )

    assert response.status_code == 200
    preflight = response.json()["preflight"]
    assert preflight["capability"] == "IMAGE_EXPRESSION"
    assert preflight["ready"] is False
    assert preflight["hero"]["media_version_id"] == hero
    assert preflight["would_create_jobs"] == 0
    assert any("IMAGE_EXPRESSION" in blocker["message"] for blocker in preflight["blockers"])
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_intents WHERE purpose='ASSET_EXPRESSION_GRID'").fetchone()[0] == 0


def test_expression_slots_freeze_hero_and_use_expression_grid_reference_kind() -> None:
    service = object.__new__(AssetExpressionService)
    plan = service._variant_plan(
        kind="HAPPY",
        yaw=0.0,
        prompt="happy joyful expression",
        hero_media_version_id="hero-version-1",
        input_role="CHARACTER_REFERENCE",
        profile_version_id="expression-profile-v3",
        asset_state_id="state-1",
        consistency_strength="HIGH",
        background="CLEAN",
        seed_index=1,
    )
    assert plan.profile_version_id == "expression-profile-v3"
    assert plan.parameter_set["SLOT_KIND"] == "HAPPY"
    assert plan.parameter_set["OUTPUT_REFERENCE_KIND"] == "EXPRESSION_GRID"
    assert plan.parameter_set["ASSET_STATE_ID"] == "state-1"
    assert plan.bindings[0].role == "CHARACTER_REFERENCE"
    assert plan.bindings[0].media_version_id == "hero-version-1"


def test_detail_preflight_is_honest_about_missing_image_edit_profile(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_detail_preflight")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_DETAIL", "Detail Character")
    hero = _image(workspace, database, project_id, "detail-hero.png")
    _write_service(database, lambda service: service.add_reference(project_id, str(asset["id"]), hero, "HERO", is_locked=True))
    with TestClient(create_app(workspace)) as client:
        response = client.post(f"/api/v1/story-assets/{asset['id']}/generate-detail:preflight", json={"consistency_strength": "HIGH", "background": "CLEAN"})
    assert response.status_code == 200
    preflight = response.json()["preflight"]
    assert preflight["capability"] == "IMAGE_EDIT"
    assert preflight["ready"] is False
    assert preflight["would_create_jobs"] == 0
    assert any("IMAGE_EDIT" in blocker["message"] for blocker in preflight["blockers"])


def test_detail_slots_map_to_closeup_and_detail_without_changing_hero() -> None:
    service = object.__new__(AssetDetailService)
    common = dict(yaw=0.0, hero_media_version_id="hero-version-2", input_role="REFERENCE_IMAGE", profile_version_id="image-edit-v4", asset_state_id=None, consistency_strength="HIGH", background="CLEAN")
    face = service._variant_plan(kind="FACE_CLOSEUP", prompt="face", seed_index=0, **common)
    costume = service._variant_plan(kind="COSTUME_DETAIL", prompt="costume", seed_index=1, **common)
    assert face.parameter_set["OUTPUT_REFERENCE_KIND"] == "CLOSEUP"
    assert costume.parameter_set["OUTPUT_REFERENCE_KIND"] == "DETAIL"
    assert face.bindings[0].media_version_id == costume.bindings[0].media_version_id == "hero-version-2"
    assert face.explicit_seed != costume.explicit_seed

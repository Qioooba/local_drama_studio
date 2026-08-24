from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.story_assets import StoryAssetService
from local_drama.domain.errors import DomainRuleError
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


def _image(workspace, database, project_id: str, name: str = "ref.png"):
    source = workspace.work_root / name
    source.write_bytes(PNG)
    return MediaService(database, workspace).import_file(project_id, source, media_kind="IMAGE")


def test_create_four_kinds_and_list_kind_filter(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "story_four_kinds")
    service = StoryAssetService(database, workspace)
    service.create_asset(str(project["id"]), "CHARACTER", "CHAR_MOTHER", "母亲", "短发")
    service.create_asset(str(project["id"]), "SCENE", "SCENE_KITCHEN", "厨房", "暖光")
    service.create_asset(str(project["id"]), "PROP", "PROP_LETTER", "信件", "关键道具")
    service.create_asset(str(project["id"]), "COSTUME", "COSTUME_BLUE", "蓝衣", "外套")
    assert len(service.list_assets(str(project["id"]))) == 4
    assert [item["code"] for item in service.list_assets(str(project["id"]), "SCENE")] == ["SCENE_KITCHEN"]
    assert service.list_assets(str(project["id"]), "STYLE") == []


def test_update_optimistic_lock_conflict(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "story_optimistic")
    service = StoryAssetService(database, workspace)
    asset = service.create_asset(str(project["id"]), "CHARACTER", "CHAR_A", "甲")
    updated = service.update_asset(str(asset["id"]), 1, name="甲（改）")
    assert updated["name"] == "甲（改）"
    assert updated["revision"] == 2
    with pytest.raises(DomainRuleError) as conflict:
        service.update_asset(str(asset["id"]), 1, description="过期写入")
    assert conflict.value.code == "STORY_ASSET_REVISION_CONFLICT"


def test_code_unique_conflict_and_kind_invalid(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "story_guards")
    service = StoryAssetService(database, workspace)
    service.create_asset(str(project["id"]), "CHARACTER", "CHAR_SAME", "同名角色")
    with pytest.raises(DomainRuleError) as duplicate:
        service.create_asset(str(project["id"]), "PROP", "CHAR_SAME", "道具同名")
    assert duplicate.value.code == "STORY_ASSET_CODE_CONFLICT"
    with pytest.raises(DomainRuleError) as kind:
        service.create_asset(str(project["id"]), "STYLE", "STYLE_X", "风格")
    assert kind.value.code == "STORY_ASSET_KIND_INVALID"
    with pytest.raises(DomainRuleError) as empty:
        service.create_asset(str(project["id"]), "PROP", "   ", "空 code")
    assert empty.value.code == "STORY_ASSET_FIELDS_REQUIRED"


def test_media_scope_validation(workspace, database) -> None:
    _, first_project, _, _ = _project(workspace, database, "story_media_owner")
    _, second_project, _, _ = _project(workspace, database, "story_media_foreign")
    media = _image(workspace, database, str(first_project["id"]))
    service = StoryAssetService(database, workspace)
    with pytest.raises(DomainRuleError) as cross:
        service.create_asset(str(second_project["id"]), "CHARACTER", "CHAR_X", "跨项目", canonical_media_version_id=str(media["media_version_id"]))
    assert cross.value.code == "STORY_ASSET_MEDIA_SCOPE_INVALID"
    with pytest.raises(DomainRuleError) as missing:
        service.create_asset(str(first_project["id"]), "CHARACTER", "CHAR_Y", "不存在媒体", canonical_media_version_id="00000000-0000-0000-0000-000000000000")
    assert missing.value.code == "STORY_ASSET_MEDIA_SCOPE_INVALID"
    asset = service.create_asset(
        str(first_project["id"]), "CHARACTER", "CHAR_MOTHER", "母亲",
        canonical_media_version_id=str(media["media_version_id"]),
    )
    assert asset["canonical_media_version_id"] == media["media_version_id"]


def test_archive_keeps_bindings_but_blocks_new_bindings(workspace, database) -> None:
    _, project, _, shot = _project(workspace, database, "story_archive")
    service = StoryAssetService(database, workspace)
    asset = service.create_asset(str(project["id"]), "CHARACTER", "CHAR_MOTHER", "母亲")
    service.bind_asset_to_shot(str(shot["id"]), str(asset["id"]), "main")
    archived = service.archive_asset(str(asset["id"]), 1, "测试归档")
    assert archived["status"] == "ARCHIVED"
    with pytest.raises(DomainRuleError) as bound_after:
        service.bind_asset_to_shot(str(shot["id"]), str(asset["id"]), "guest")
    assert bound_after.value.code == "STORY_ASSET_ARCHIVED"
    with pytest.raises(DomainRuleError) as twice:
        service.archive_asset(str(asset["id"]), 2, "重复归档")
    assert twice.value.code == "STORY_ASSET_ALREADY_ARCHIVED"
    bindings = service.list_shot_assets(str(shot["id"]))
    assert len(bindings) == 1
    assert bindings[0]["status"] == "ARCHIVED"


def test_bind_duplicate_conflict_and_unbind(workspace, database) -> None:
    _, project, _, shot = _project(workspace, database, "story_bindings")
    service = StoryAssetService(database, workspace)
    asset = service.create_asset(str(project["id"]), "PROP", "PROP_LETTER", "信件")
    binding = service.bind_asset_to_shot(str(shot["id"]), str(asset["id"]), "main")
    with pytest.raises(DomainRuleError) as duplicate:
        service.bind_asset_to_shot(str(shot["id"]), str(asset["id"]), "main")
    assert duplicate.value.code == "STORY_ASSET_ALREADY_BOUND"
    service.bind_asset_to_shot(str(shot["id"]), str(asset["id"]), "opened")
    assert len(service.list_shot_assets(str(shot["id"]))) == 2
    result = service.unbind_asset_from_shot(str(binding["binding_id"]))
    assert result["unbound"] is True
    assert [item["role_in_shot"] for item in service.list_shot_assets(str(shot["id"]))] == ["opened"]
    with pytest.raises(DomainRuleError) as missing_binding:
        service.unbind_asset_from_shot(str(binding["binding_id"]))
    assert missing_binding.value.code == "SHOT_ASSET_BINDING_NOT_FOUND"


def test_shot_asset_summary_and_asset_shot_listing(workspace, database) -> None:
    _, project, _, shot = _project(workspace, database, "story_summary")
    service = StoryAssetService(database, workspace)
    asset = service.create_asset(str(project["id"]), "SCENE", "SCENE_HALL", "大厅", "正门")
    service.bind_asset_to_shot(str(shot["id"]), str(asset["id"]), "location")
    item = service.list_shot_assets(str(shot["id"]))[0]
    assert item["asset_id"] == asset["id"]
    assert item["name"] == "大厅"
    assert item["code"] == "SCENE_HALL"
    assert item["kind"] == "SCENE"
    assert item["status"] == "ACTIVE"
    assert item["canonical_media_version_id"] is None
    assert item["role_in_shot"] == "location"
    shot_rows = service.list_asset_shots(str(asset["id"]))
    assert shot_rows[0]["shot_id"] == shot["id"]
    assert shot_rows[0]["shot_code"] == "S001"
    assert shot_rows[0]["episode_code"] == "EPISODE_001"


def test_audit_events_persisted(workspace, database) -> None:
    _, project, _, shot = _project(workspace, database, "story_audit")
    service = StoryAssetService(database, workspace)
    asset = service.create_asset(str(project["id"]), "CHARACTER", "CHAR_MOTHER", "母亲")
    binding = service.bind_asset_to_shot(str(shot["id"]), str(asset["id"]), "main")
    with database.connect() as connection:
        created = connection.execute(
            "SELECT action,subject_type,summary,metadata_redacted_json FROM audit_events WHERE subject_type='story_asset' AND subject_id=?",
            (asset["id"],),
        ).fetchone()
        bound = connection.execute(
            "SELECT action,subject_type FROM audit_events WHERE subject_type='shot_asset_binding' AND subject_id=?",
            (binding["binding_id"],),
        ).fetchone()
        archived_after = service.archive_asset(str(asset["id"]), 1, "并发归档")
        archived = connection.execute(
            "SELECT action,subject_type FROM audit_events WHERE subject_type='story_asset' AND subject_id=? AND action='STORY_ASSET_ARCHIVED'",
            (archived_after["id"],),
        ).fetchone()
    assert created["action"] == "STORY_ASSET_CREATED"
    assert "创建故事资产卡" in created["summary"]
    assert bound["action"] == "SHOT_ASSET_BOUND"
    assert archived["action"] == "STORY_ASSET_ARCHIVED"


def test_not_found_errors(workspace, database) -> None:
    _, project, _, shot = _project(workspace, database, "story_missing")
    service = StoryAssetService(database, workspace)
    with pytest.raises(DomainRuleError) as asset_missing:
        service.get_asset("00000000-0000-0000-0000-000000000000")
    assert asset_missing.value.code == "STORY_ASSET_NOT_FOUND"
    with pytest.raises(DomainRuleError) as project_missing:
        service.list_assets("00000000-0000-0000-0000-000000000000")
    assert project_missing.value.code == "PROJECT_NOT_FOUND"
    with pytest.raises(DomainRuleError) as shot_missing:
        service.bind_asset_to_shot("00000000-0000-0000-0000-000000000000", "00000000-0000-0000-0000-000000000000")
    assert shot_missing.value.code == "SHOT_NOT_FOUND"
    with pytest.raises(DomainRuleError) as bind_missing_asset:
        service.bind_asset_to_shot(str(shot["id"]), "00000000-0000-0000-0000-000000000000")
    assert bind_missing_asset.value.code == "STORY_ASSET_NOT_FOUND"
    with pytest.raises(DomainRuleError) as shot_list_missing:
        service.list_shot_assets("00000000-0000-0000-0000-000000000000")
    assert shot_list_missing.value.code == "SHOT_NOT_FOUND"


def test_story_asset_api_full_flow(workspace, database) -> None:
    _, project, _, shot = _project(workspace, database, "story_api")
    media = _image(workspace, database, str(project["id"]), "story-api.png")
    with TestClient(create_app(workspace)) as client:
        created = client.post(
            f"/api/v1/projects/{project['id']}/story-assets",
            json={"kind": "CHARACTER", "code": "CHAR_MOTHER", "name": "母亲", "description": "短发", "canonical_media_version_id": media["media_version_id"]},
        )
        listed = client.get(f"/api/v1/projects/{project['id']}/story-assets", params={"kind": "CHARACTER"})
        single = client.get(f"/api/v1/story-assets/{created.json()['asset']['id']}")
        updated = client.patch(
            f"/api/v1/story-assets/{created.json()['asset']['id']}",
            json={"expected_revision": 1, "name": "母亲（改）"},
        )
        conflict = client.patch(
            f"/api/v1/story-assets/{created.json()['asset']['id']}",
            json={"expected_revision": 1, "description": "过期写入"},
        )
        bound = client.post(
            f"/api/v1/shots/{shot['id']}/story-asset-bindings",
            json={"asset_id": created.json()["asset"]["id"], "role_in_shot": "main"},
        )
        duplicate_bind = client.post(
            f"/api/v1/shots/{shot['id']}/story-asset-bindings",
            json={"asset_id": created.json()["asset"]["id"], "role_in_shot": "main"},
        )
        shot_bindings = client.get(f"/api/v1/shots/{shot['id']}/story-asset-bindings")
        archived = client.post(
            f"/api/v1/story-assets/{created.json()['asset']['id']}:archive",
            json={"expected_revision": 2, "reason": "验收归档"},
        )
        restored = client.post(
            f"/api/v1/story-assets/{created.json()['asset']['id']}:restore",
            json={"expected_revision": 3, "reason": "验收恢复"},
        )
        unbound = client.delete(f"/api/v1/story-asset-bindings/{bound.json()['binding']['binding_id']}")
        after_unbind = client.get(f"/api/v1/shots/{shot['id']}/story-asset-bindings")
        missing = client.get("/api/v1/story-assets/00000000-0000-0000-0000-000000000000")

    assert created.status_code == 201
    assert created.json()["asset"]["canonical_media_version_id"] == media["media_version_id"]
    assert listed.status_code == 200
    assert listed.json()["items"][0]["code"] == "CHAR_MOTHER"
    assert single.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["asset"]["name"] == "母亲（改）"
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "STORY_ASSET_REVISION_CONFLICT"
    assert bound.status_code == 201
    assert bound.json()["binding"]["role_in_shot"] == "main"
    assert duplicate_bind.status_code == 409
    assert duplicate_bind.json()["error"]["code"] == "STORY_ASSET_ALREADY_BOUND"
    assert shot_bindings.status_code == 200
    assert len(shot_bindings.json()["items"]) == 1
    assert shot_bindings.json()["items"][0]["asset_id"] == created.json()["asset"]["id"]
    assert archived.status_code == 201
    assert archived.json()["asset"]["status"] == "ARCHIVED"
    assert restored.status_code == 201
    assert restored.json()["asset"]["status"] == "ACTIVE"
    assert unbound.status_code == 200
    assert unbound.json()["unbound"] is True
    assert after_unbind.status_code == 200
    assert after_unbind.json()["items"] == []
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "STORY_ASSET_NOT_FOUND"


def test_api_rejects_kind_and_project_scope(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "story_api_guards")
    with TestClient(create_app(workspace)) as client:
        bad_kind = client.post(
            f"/api/v1/projects/{project['id']}/story-assets",
            json={"kind": "STYLE", "code": "STYLE_X", "name": "风格"},
        )
        missing_project = client.get("/api/v1/projects/00000000-0000-0000-0000-000000000000/story-assets")
    assert bad_kind.status_code == 422
    assert bad_kind.json()["error"]["code"] == "STORY_ASSET_KIND_INVALID"
    assert missing_project.status_code == 404
    assert missing_project.json()["error"]["code"] == "PROJECT_NOT_FOUND"

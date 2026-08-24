from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from local_drama.application.configuration import ConfigurationService
from local_drama.application.delivery_presets import DELIVERY_PRESET_BY_CODE
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app

REQUIRED_PRESET_CODES = {
    "DOUYIN_VERTICAL",
    "KUAI_SHOU_VERTICAL",
    "XIAOHONGSHU_3_4",
    "WECHAT_CHANNELS",
    "BILIBILI_HORIZONTAL",
    "UNIVERSAL_16_9",
    "UNIVERSAL_9_16",
}

SPEC_FIELDS = {
    "path_rel",
    "width",
    "height",
    "fps",
    "bitrate_kbps",
    "max_duration_seconds",
    "cover_aspect",
    "audio",
    "subtitles",
}


def _project(workspace, database, code: str = "preset_project") -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title="Preset project",
        episode_count=1,
        aspect_ratio="9:16",
        fps_num=30,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )


def test_preset_table_is_complete_and_field_typed(workspace, database) -> None:
    service = ConfigurationService(database)
    items = service.list_delivery_presets()["items"]
    assert items, "预设表不能为空"
    codes = {item["code"] for item in items}
    assert REQUIRED_PRESET_CODES <= codes
    for item in items:
        assert item["code"] and item["title"] and item["description"]
        spec = item["spec"]
        assert SPEC_FIELDS <= set(spec), f"{item['code']} spec 缺字段: {SPEC_FIELDS - set(spec)}"
        for field in ("width", "height", "fps", "bitrate_kbps", "max_duration_seconds"):
            assert isinstance(spec[field], int) and spec[field] > 0, f"{item['code']}.{field} 必须是正整数"
        assert isinstance(spec["cover_aspect"], str) and "x" in spec["cover_aspect"]
        assert isinstance(spec["path_rel"], str) and not spec["path_rel"].startswith("/") and ".." not in spec["path_rel"]
        assert spec["audio"] and spec["subtitles"]


def test_list_delivery_presets_via_api(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/delivery-presets")
    assert response.status_code == 200
    body = response.json()
    codes = {item["code"] for item in body["items"]}
    assert REQUIRED_PRESET_CODES <= codes
    douyin = next(item for item in body["items"] if item["code"] == "DOUYIN_VERTICAL")
    assert douyin["spec"]["width"] == 1080
    assert douyin["spec"]["height"] == 1920
    assert douyin["spec"]["fps"] == 30
    assert douyin["spec"]["bitrate_kbps"] == 6000
    assert douyin["spec"]["max_duration_seconds"] == 180
    assert douyin["spec"]["cover_aspect"] == "1080x1440"


def test_create_from_preset_persists_spec_and_audit(workspace, database) -> None:
    project = _project(workspace, database)
    project_id = str(project["id"])
    service = ConfigurationService(database)
    target = service.create_delivery_target_from_preset(project_id, "DOUYIN_VERTICAL", "抖音交付", actor="tester")
    assert target["code"] == "DOUYIN_VERTICAL"
    assert target["title"] == "抖音交付"
    assert target["transport"] == "LOCAL_FILESYSTEM"
    assert target["preset_code"] == "DOUYIN_VERTICAL"
    assert target["status"] == "ACTIVE"
    expected_spec = dict(DELIVERY_PRESET_BY_CODE["DOUYIN_VERTICAL"].spec)
    assert target["spec"] == expected_spec
    with database.connect() as connection:
        row = connection.execute(
            "SELECT dtv.target_spec_json, dt.transport FROM delivery_target_versions dtv "
            "JOIN delivery_targets dt ON dt.id=dtv.delivery_target_id WHERE dtv.id=?",
            (target["version_id"],),
        ).fetchone()
        assert row is not None
        assert json.loads(row["target_spec_json"]) == expected_spec
        assert row["transport"] == "LOCAL_FILESYSTEM"
        created = connection.execute(
            "SELECT summary, metadata_redacted_json FROM audit_events WHERE action='DELIVERY_TARGET_CREATED' AND subject_id=?",
            (target["id"],),
        ).fetchone()
        assert created is not None  # base audit reused from create_delivery_target
        preset_audit = connection.execute(
            "SELECT summary, metadata_redacted_json FROM audit_events WHERE action='DELIVERY_TARGET_CREATED_FROM_PRESET' AND subject_id=?",
            (target["id"],),
        ).fetchone()
        assert preset_audit is not None
        metadata = json.loads(preset_audit["metadata_redacted_json"])
        assert metadata["preset_code"] == "DOUYIN_VERTICAL"
        assert metadata["project_id"] == project_id
        assert metadata["title"] == "抖音交付"
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v1/projects/{project_id}/delivery-targets:from-preset",
            json={"preset_code": "KUAI_SHOU_VERTICAL", "title": "快手交付"},
        )
        assert response.status_code == 201
        api_target = response.json()["target"]
        assert api_target["code"] == "KUAI_SHOU_VERTICAL"
        assert api_target["spec"]["width"] == 1080
        assert api_target["spec"]["height"] == 1920
        assert api_target["spec"]["bitrate_kbps"] == 5000


def test_create_from_preset_rejects_unknown_preset(workspace, database) -> None:
    project = _project(workspace, database)
    project_id = str(project["id"])
    service = ConfigurationService(database)
    with pytest.raises(DomainRuleError) as error:
        service.create_delivery_target_from_preset(project_id, "NOT_A_PLATFORM", "非法预设")
    assert error.value.code == "DELIVERY_PRESET_UNSUPPORTED"
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v1/projects/{project_id}/delivery-targets:from-preset",
            json={"preset_code": "NOT_A_PLATFORM", "title": "非法预设"},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DELIVERY_PRESET_UNSUPPORTED"


def test_create_from_preset_rejects_missing_project(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            "/api/v1/projects/missing/delivery-targets:from-preset",
            json={"preset_code": "DOUYIN_VERTICAL", "title": "无项目"},
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROJECT_NOT_FOUND"


def test_preset_target_is_automatically_current_and_appears_in_configuration_snapshot(workspace, database) -> None:
    """Choosing a preset is sufficient: creation also makes it current."""
    project = _project(workspace, database)
    project_id = str(project["id"])
    service = ConfigurationService(database)
    target = service.create_delivery_target_from_preset(project_id, "XIAOHONGSHU_3_4", "小红书交付")
    snapshot_before = service.inspect_project_configuration(project_id)
    assert snapshot_before["selected_delivery_target_version_id"] == target["version_id"]
    snapshot = service.inspect_project_configuration(project_id)
    assert snapshot["selected_delivery_target_version_id"] == target["version_id"]
    selected_item = next(
        item for item in snapshot["delivery_targets"] if item["version_id"] == target["version_id"]
    )
    assert selected_item["code"] == "XIAOHONGSHU_3_4"
    assert selected_item["spec"] == dict(DELIVERY_PRESET_BY_CODE["XIAOHONGSHU_3_4"].spec)
    with TestClient(create_app(workspace)) as client:
        response = client.get(f"/api/v1/projects/{project_id}/configuration")
    assert response.status_code == 200
    assert response.json()["configuration"]["selected_delivery_target_version_id"] == target["version_id"]

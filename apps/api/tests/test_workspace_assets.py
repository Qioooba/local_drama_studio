from __future__ import annotations

import pytest

from local_drama.application.g7_readiness import G7ReadinessService
from local_drama.application.projects import ProjectService
from local_drama.application.workspace_assets import WorkspaceAssetService
from local_drama.domain.errors import DomainRuleError


def test_workspace_asset_authorization_rehashes_media_and_publishes_brand_kit(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="asset_auth", title="Asset auth", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    source = workspace.work_root / "brand-reference.png"
    source.write_bytes(b"brand-reference-image")
    from local_drama.application.media import MediaService
    media = MediaService(database, workspace).import_file(str(project["id"]), source, media_kind="IMAGE")
    service = WorkspaceAssetService(database, workspace)
    authorization = service.authorize_media_version(str(project["id"]), str(media["media_version_id"]))
    duplicate = service.authorize_media_version(str(project["id"]), str(media["media_version_id"]))
    assert authorization["authorization_status"] == "AUTHORIZED"
    assert duplicate["duplicate"] is True
    kit = service.create_brand_kit(str(project["id"]), "localdramastudio", "LocalDramaStudio", {"colors": {"canvas": "#F4F1EB"}, "typography": {"body": "system"}})
    assert kit["status"] == "ACTIVE"
    readiness = G7ReadinessService(database).inspect(str(project["id"]))
    assert next(item for item in readiness["checks"] if item["code"] == "WORKSPACE_ASSET_AUTHORIZATION")["passed"] is True


def test_workspace_asset_authorization_rejects_cross_project_and_tamper(workspace, database) -> None:
    first = ProjectService(database, workspace.projects_root).create_project(code="asset_first", title="first", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True)
    second = ProjectService(database, workspace.projects_root).create_project(code="asset_second", title="second", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True)
    source = workspace.work_root / "asset.txt"
    source.write_text("authorized", encoding="utf-8")
    from local_drama.application.media import MediaService
    media = MediaService(database, workspace).import_file(str(first["id"]), source, media_kind="DOCUMENT")
    with pytest.raises(DomainRuleError) as raised:
        WorkspaceAssetService(database, workspace).authorize_media_version(str(second["id"]), str(media["media_version_id"]))
    assert raised.value.code == "WORKSPACE_ASSET_PROJECT_MISMATCH"
    content_path = MediaService(database, workspace).content_path(str(media["media_version_id"]))[1]
    content_path.write_text("tampered", encoding="utf-8")
    with pytest.raises(DomainRuleError) as raised:
        WorkspaceAssetService(database, workspace).authorize_media_version(str(first["id"]), str(media["media_version_id"]))
    assert raised.value.code == "WORKSPACE_ASSET_INTEGRITY_FAILED"

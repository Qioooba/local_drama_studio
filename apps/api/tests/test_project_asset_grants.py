from __future__ import annotations

import pytest

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.workspace_assets import WorkspaceAssetService
from local_drama.domain.errors import DomainRuleError


def test_cross_project_asset_grant_freezes_source_and_reports_withdrawal(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    source_project = projects.create_project(code="grant_source", title="Grant source", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True)
    target_project = projects.create_project(code="grant_target", title="Grant target", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True)
    source = workspace.work_root / "shared-reference.txt"
    source.write_text("local-only shared reference", encoding="utf-8")
    media = MediaService(database, workspace).import_file(str(source_project["id"]), source, media_kind="DOCUMENT")
    assets = WorkspaceAssetService(database, workspace)
    authorization = assets.authorize_media_version(str(source_project["id"]), str(media["media_version_id"]))
    assert assets.list_authorizations(str(source_project["id"]))[0]["usable"] is True
    candidates = assets.list_grant_candidates(str(target_project["id"]))
    candidate = next(item for item in candidates if item["authorization_id"] == authorization["id"])
    assert candidate["grantable"] is True
    grant = assets.create_grant(str(target_project["id"]), str(authorization["id"]), "DERIVED")
    assert grant["status"] == "ACTIVE"
    assert assets.list_grants(str(target_project["id"]))[0]["usable"] is True
    revoked = assets.revoke_authorization(str(source_project["id"]), str(media["media_version_id"]), "源项目撤回共享")
    assert revoked["authorization_status"] == "REVOKED"
    assert assets.list_authorizations(str(source_project["id"]))[0]["usable"] is False
    impacted = assets.list_grants(str(target_project["id"]))[0]
    assert "SOURCE_AUTHORIZATION_REVOKED" in impacted["impact"]
    assert impacted["usable"] is False
    with pytest.raises(DomainRuleError) as raised:
        assets.create_grant(str(target_project["id"]), str(authorization["id"]), "READ_ONLY")
    assert raised.value.code == "ASSET_GRANT_SOURCE_REVOKED"

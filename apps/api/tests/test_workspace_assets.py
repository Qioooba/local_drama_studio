from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from local_drama.application.g7_readiness import G7ReadinessService
from local_drama.application.projects import ProjectService
from local_drama.application.worker import LocalMediaWorker
from local_drama.application.workspace_assets import WorkspaceAssetService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


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


def test_image_content_endpoint_requires_derived_thumbnail(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="thumb_policy", title="Thumbnail policy", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    source = workspace.work_root / "reference.png"
    subprocess.run([workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=red:s=16x16:d=1", "-frames:v", "1", "-y", str(source)], check=True, capture_output=True)
    from local_drama.application.media import MediaService
    media = MediaService(database, workspace).import_file(str(project["id"]), source, media_kind="IMAGE")
    with TestClient(create_app(workspace)) as client:
        response = client.get(f"/api/v1/media-versions/{media['media_version_id']}/content")
        head_response = client.head(f"/api/v1/media-versions/{media['media_version_id']}/content")
        pending_poster = client.get(f"/api/v1/media-versions/{media['media_version_id']}/thumbnail")
        submission = client.post(
            f"/api/v1/media-versions/{media['media_version_id']}/derivatives:submit",
            params={"kind": "THUMBNAIL", "size": "small", "frame": "poster"},
        )
        assert pending_poster.status_code == 409
        assert pending_poster.json()["error"]["code"] == "MEDIA_DERIVATIVE_NOT_READY"
        assert submission.status_code == 202
        outcome = LocalMediaWorker(database, workspace).run_once("thumbnail-policy-worker", ["CPU"])
        assert outcome is not None and outcome["result"]["job_state"] == "SUCCEEDED"
        poster = client.get(f"/api/v1/media-versions/{media['media_version_id']}/thumbnail")
        assert poster.status_code == 200
        MediaService(database, workspace).content_path(str(media["media_version_id"]))[1].write_bytes(b"tampered-image")
        tampered_poster = client.get(f"/api/v1/media-versions/{media['media_version_id']}/thumbnail")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IMAGE_CONTENT_REQUIRES_THUMBNAIL"
    assert head_response.status_code == 409
    assert response.json()["error"]["details"]["thumbnail_path"].endswith("/thumbnail?size=small&frame=poster")
    assert tampered_poster.status_code == 422
    assert tampered_poster.json()["error"]["code"] == "SOURCE_INTEGRITY_FAILED"


def test_image_mime_cannot_be_imported_as_document_to_bypass_thumbnail_policy(workspace, database) -> None:
    """Image bytes cannot enter the workspace under a non-image media kind."""
    project = ProjectService(database, workspace.projects_root).create_project(
        code="thumb_mime_guard", title="Thumbnail MIME guard", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    source = workspace.work_root / "mime-guard.png"
    subprocess.run([workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=blue:s=16x16:d=1", "-frames:v", "1", "-y", str(source)], check=True, capture_output=True)
    from local_drama.application.media import MediaService
    with pytest.raises(DomainRuleError) as raised:
        MediaService(database, workspace).import_file(str(project["id"]), source, media_kind="DOCUMENT")
    assert raised.value.code == "MEDIA_KIND_MISMATCH"


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

from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _project(workspace, database, code: str):
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1000,
        allow_unconfigured_capabilities=True,
    )


def test_media_catalogue_is_project_scoped_searchable_and_uploads_without_client_paths(workspace, database) -> None:
    first = _project(workspace, database, "picker_first")
    second = _project(workspace, database, "picker_second")
    first_source = workspace.work_root / "hero-alpha.png"
    second_source = workspace.work_root / "other-project.png"
    subprocess.run([workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=red:s=16x16:d=1", "-frames:v", "1", "-y", str(first_source)], check=True, capture_output=True)
    subprocess.run([workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=blue:s=16x16:d=1", "-frames:v", "1", "-y", str(second_source)], check=True, capture_output=True)
    first_media = MediaService(database, workspace).import_file(str(first["id"]), first_source, media_kind="IMAGE", purpose="ASSET_REFERENCE")
    MediaService(database, workspace).import_file(str(second["id"]), second_source, media_kind="IMAGE", purpose="ASSET_REFERENCE")
    mislabeled_source = workspace.work_root / "not-a-video.json"
    mislabeled_source.write_text('{"kind":"report"}', encoding="utf-8")
    with pytest.raises(DomainRuleError, match="声明的媒体类型") as mismatch:
        MediaService(database, workspace).import_file(str(first["id"]), mislabeled_source, media_kind="VIDEO", purpose="LEGACY_BAD_RECORD")
    assert mismatch.value.code == "MEDIA_KIND_MISMATCH"
    legacy_media = MediaService(database, workspace).import_file(str(first["id"]), mislabeled_source, media_kind="OTHER", purpose="LEGACY_BAD_RECORD")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE media_assets SET media_kind='VIDEO' WHERE id=(SELECT media_asset_id FROM media_versions WHERE id=?)",
            (legacy_media["media_version_id"],),
        )

    with TestClient(create_app(workspace)) as client:
        catalogue = client.get(f"/api/v1/projects/{first['id']}/media-catalogue", params={"q": "alpha", "media_kind": "IMAGE"})
        upload_source = workspace.work_root / "new-reference.png"
        subprocess.run([workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=green:s=16x16:d=1", "-frames:v", "1", "-y", str(upload_source)], check=True, capture_output=True)
        upload = client.post(
            f"/api/v1/projects/{first['id']}/media:upload",
            content=upload_source.read_bytes(),
            headers={"Content-Type": "image/png", "X-File-Name": "new-reference.png"},
        )
        invalid = client.post(
            f"/api/v1/projects/{first['id']}/media:upload",
            content=b"not-an-image",
            headers={"X-File-Name": "unsafe.exe"},
        )
        path_source = workspace.work_root / "path-reference.png"
        subprocess.run([workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=yellow:s=16x16:d=1", "-frames:v", "1", "-y", str(path_source)], check=True, capture_output=True)
        path_import = client.post(
            "/api/v1/media:import",
            json={
                "project_id": str(first["id"]),
                "source_path": str(path_source),
                "purpose": "ASSET_REFERENCE",
                "owner_type": "PROJECT",
                "owner_id": str(first["id"]),
                "media_kind": "IMAGE",
                "stage": "IMPORTED",
            },
        )
        uploaded_catalogue = client.get(f"/api/v1/projects/{first['id']}/media-catalogue", params={"q": "new-reference"})
        path_catalogue = client.get(f"/api/v1/projects/{first['id']}/media-catalogue", params={"q": "path-reference"})
        video_catalogue = client.get(f"/api/v1/projects/{first['id']}/media-catalogue", params={"media_kind": "VIDEO"})
        legacy_thumbnail = client.get(f"/api/v1/media-versions/{legacy_media['media_version_id']}/thumbnail?size=small&frame=poster")

    assert catalogue.status_code == 200
    assert [item["media_version_id"] for item in catalogue.json()["items"]] == [first_media["media_version_id"]]
    assert "rel_path" not in catalogue.json()["items"][0]
    assert "sha256" not in catalogue.json()["items"][0]
    assert upload.status_code == 201
    assert upload.json()["media"]["media_version_id"] == uploaded_catalogue.json()["items"][0]["media_version_id"]
    assert uploaded_catalogue.json()["items"][0]["source_name"] == "new-reference.png"
    assert path_import.status_code == 201
    assert path_import.json()["media"]["media_version_id"] == path_catalogue.json()["items"][0]["media_version_id"]
    assert path_catalogue.json()["items"][0]["source_name"] == "path-reference.png"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "MEDIA_UPLOAD_TYPE_INVALID"
    assert video_catalogue.status_code == 200
    assert video_catalogue.json()["items"] == []
    assert legacy_thumbnail.status_code == 422
    assert legacy_thumbnail.json()["error"]["code"] == "MEDIA_KIND_MIME_MISMATCH"

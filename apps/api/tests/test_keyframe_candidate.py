from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.main import create_app

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082")


def test_real_image_creates_idempotent_shot_keyframe_candidate(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="keyframe_candidate", title="Keyframe candidate", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    with database.connect() as connection:
        episode_id = str(
            connection.execute(
                """SELECT e.id FROM episodes e JOIN seasons s ON s.id=e.season_id
                WHERE s.project_id=? LIMIT 1""",
                (project["id"],),
            ).fetchone()[0]
        )
    shot_id = str(ProjectService(database, workspace.projects_root).create_shot(episode_id, "SH-001", 4_000)["id"])
    source_path = Path(workspace.work_root) / "frame.png"
    source_path.write_bytes(PNG)
    imported = MediaService(database, workspace).import_file(str(project["id"]), source_path, media_kind="IMAGE")
    service = MediaService(database, workspace)
    first = service.create_keyframe_candidate(str(imported["media_version_id"]), shot_id)
    second = service.create_keyframe_candidate(str(imported["media_version_id"]), shot_id)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["id"] == first["id"]
    assert first["stage"] == "KEYFRAME"
    assert first["owner_type"] == "SHOT"
    assert first["owner_id"] == shot_id
    assert first["parent_version_id"] == imported["media_version_id"]
    assert first["approved_version_id"] is None

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v1/media-versions/{imported['media_version_id']}:create-keyframe-candidate",
            json={"shot_id": shot_id},
        )
    assert response.status_code == 201
    assert response.json()["media"]["duplicate"] is True


def test_video_cannot_be_selected_as_keyframe(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="keyframe_reject", title="Keyframe reject", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    source = workspace.work_root / "not-a-keyframe.mp4"
    source.write_bytes(b"video")
    imported = MediaService(database, workspace).import_file(str(project["id"]), source, media_kind="VIDEO", stage="PROXY")
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v1/media-versions/{imported['media_version_id']}:select",
            json={"selection_type": "KEYFRAME"},
        )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "INVALID_KEYFRAME_SELECTION"

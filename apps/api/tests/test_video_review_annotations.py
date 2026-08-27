from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService
from local_drama.application.timeline import TimelineService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _project_video(workspace, database, code: str) -> tuple[dict[str, object], dict[str, object]]:
    project = ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title="Video annotation",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=2_000,
        allow_unconfigured_capabilities=True,
    )
    source = workspace.work_root / f"{code}.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=blue:s=160x90:d=1", "-pix_fmt", "yuv420p", "-an", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    media = MediaService(database, workspace).import_file(str(project["id"]), source, media_kind="VIDEO", stage="PROXY")
    return project, media


def test_video_annotation_validates_time_snapshot_and_rework_project(workspace, database) -> None:
    project, media = _project_video(workspace, database, "video_annotation")
    media_version_id = str(media["media_version_id"])
    anchor = TimelineService(database, workspace).create_frame_anchor(media_version_id, position_mode="FIRST_FRAME")
    job = JobService(database, workspace).create_job(
        str(project["id"]), "VIDEO_REWORK", "MEDIA_VERSION", media_version_id, "CPU_TEST", {}, "video-annotation-rework"
    )
    reviews = ReviewService(database, workspace)
    created = reviews.create_video_annotation(
        media_version_id,
        500,
        "flicker",
        "00:00.500 亮度跳变",
        snapshot_media_version_id=str(anchor["extracted_media_version_id"]),
        rework_job_id=str(job["id"]),
    )
    assert created["category"] == "FLICKER"
    assert created["timecode_ms"] == 500
    assert reviews.list_video_annotations(media_version_id) == [created]

    with pytest.raises(DomainRuleError, match="时间码必须位于视频时长范围内"):
        reviews.create_video_annotation(media_version_id, 1_000, "OTHER", "out of range")

    other_project, _ = _project_video(workspace, database, "video_annotation_other")
    foreign_job = JobService(database, workspace).create_job(
        str(other_project["id"]), "VIDEO_REWORK", "PROJECT", str(other_project["id"]), "CPU_TEST", {}, "foreign-rework"
    )
    with pytest.raises(DomainRuleError, match="返工 Job 必须存在且属于同一项目"):
        reviews.create_video_annotation(media_version_id, 100, "MOTION", "foreign", rework_job_id=str(foreign_job["id"]))


def test_video_annotation_api_is_audited_and_lists_in_time_order(workspace, database) -> None:
    _, media = _project_video(workspace, database, "video_annotation_api")
    media_version_id = str(media["media_version_id"])
    with database.connect() as connection:
        revision = int(connection.execute(
            """SELECT ma.revision FROM media_assets ma JOIN media_versions mv
            ON mv.media_asset_id=ma.id WHERE mv.id=?""", (media_version_id,)
        ).fetchone()[0])
    with TestClient(create_app(workspace)) as client:
        for timecode_ms in (700, 100):
            response = client.post(
                f"/api/v2/review-targets/MEDIA_VERSION/{media_version_id}/annotations",
                json={"expected_revision": revision, "timecode_ms": timecode_ms, "category": "ARTIFACT", "comment": f"artifact at {timecode_ms}", "idempotency_key": f"annotation-{timecode_ms}"},
            )
            assert response.status_code == 201
        listed = client.get(f"/api/v2/review-targets/MEDIA_VERSION/{media_version_id}/annotations")
        assert listed.status_code == 200
        assert [item["timecode_ms"] for item in listed.json()["items"]] == [100, 700]
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='REVIEW_ANNOTATION_CREATED'").fetchone()[0] == 2

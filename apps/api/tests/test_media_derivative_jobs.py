from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.worker import LocalMediaWorker
from local_drama.main import create_app


def _project(workspace, database) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code="derived_media",
        title="Derived Media",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )


def test_thumbnail_get_is_read_only_until_idempotent_worker_job_finishes(workspace, database, monkeypatch) -> None:
    project = _project(workspace, database)
    source = workspace.work_root / "derived-source.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=blue:s=160x90:d=1", "-pix_fmt", "yuv420p", "-an", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    media = MediaService(database, workspace).import_file(str(project["id"]), source)
    media_version_id = str(media["media_version_id"])

    ffmpeg_calls = 0
    original = MediaService._run_ffmpeg

    def tracked(self, args):
        nonlocal ffmpeg_calls
        ffmpeg_calls += 1
        return original(self, args)

    monkeypatch.setattr(MediaService, "_run_ffmpeg", tracked)
    with TestClient(create_app(workspace)) as client:
        missing = client.get(f"/api/v1/media-versions/{media_version_id}/thumbnail?size=small&frame=poster")
        first = client.post(f"/api/v1/media-versions/{media_version_id}/derivatives:submit", params={"kind": "THUMBNAIL", "size": "small", "frame": "poster"})
        replay = client.post(f"/api/v1/media-versions/{media_version_id}/derivatives:submit", params={"kind": "THUMBNAIL", "size": "small", "frame": "poster"})
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "MEDIA_DERIVATIVE_NOT_READY"
    assert ffmpeg_calls == 0
    assert first.status_code == 202 and replay.status_code == 202
    assert first.json()["job"]["id"] == replay.json()["job"]["id"]
    assert replay.json()["job"]["idempotent_replay"] is True

    result = LocalMediaWorker(database, workspace).run_once("derived-media-worker")
    assert result is not None
    assert result["job"]["id"] == first.json()["job"]["id"]
    assert result["artifact"]["kind"] == "MEDIA_DERIVATIVE_REPORT"
    with TestClient(create_app(workspace)) as client:
        ready = client.get(f"/api/v1/media-versions/{media_version_id}/thumbnail?size=small&frame=poster")
    assert ready.status_code == 200
    assert ready.headers["content-type"].startswith("image/webp")


def test_import_can_schedule_default_derivatives_without_duplicate_jobs(workspace, database) -> None:
    project = _project(workspace, database)
    source = workspace.work_root / "auto-derived-source.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=green:s=160x90:d=1", "-pix_fmt", "yuv420p", "-an", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    service = MediaService(database, workspace)
    imported = service.import_file(str(project["id"]), source, schedule_derivatives=True)
    replayed = service.submit_default_derivatives(str(imported["media_version_id"]))
    assert [(job["input_snapshot"]["kind"], job["input_snapshot"]["size"]) for job in imported["derivative_jobs"]] == [
        ("THUMBNAIL", "small"), ("THUMBNAIL", "medium"), ("FILMSTRIP", "small"), ("PROXY", "small"),
    ]
    assert [job["id"] for job in imported["derivative_jobs"]] == [job["id"] for job in replayed]
    assert all(job["idempotent_replay"] is True for job in replayed)


def test_proxy_stage_video_also_schedules_creator_playback_proxy(workspace, database) -> None:
    project = _project(workspace, database)
    source = workspace.work_root / "proxy-stage-source.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=purple:s=160x90:d=1", "-pix_fmt", "yuv420p", "-an", "-y", str(source)],
        check=True,
        capture_output=True,
    )

    imported = MediaService(database, workspace).import_file(
        str(project["id"]), source, stage="PROXY", schedule_derivatives=True
    )

    assert [(job["input_snapshot"]["kind"], job["input_snapshot"]["size"]) for job in imported["derivative_jobs"]] == [
        ("THUMBNAIL", "small"), ("THUMBNAIL", "medium"), ("FILMSTRIP", "small"), ("PROXY", "small"),
    ]


def test_proxy_get_is_read_only_and_supports_range_after_worker_materializes(workspace, database) -> None:
    project = _project(workspace, database)
    source = workspace.work_root / "proxy-source.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=orange:s=640x360:d=1", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest", "-pix_fmt", "yuv420p", "-c:a", "aac", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    media = MediaService(database, workspace).import_file(str(project["id"]), source)
    media_version_id = str(media["media_version_id"])

    with TestClient(create_app(workspace)) as client:
        missing = client.get(f"/api/v1/media-versions/{media_version_id}/proxy")
        submitted = client.post(
            f"/api/v1/media-versions/{media_version_id}/derivatives:submit",
            params={"kind": "PROXY"},
        )
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "MEDIA_DERIVATIVE_NOT_READY"
    assert submitted.status_code == 202

    result = LocalMediaWorker(database, workspace).run_once("proxy-worker")
    assert result is not None
    assert result["job"]["id"] == submitted.json()["job"]["id"]
    with TestClient(create_app(workspace)) as client:
        ready = client.get(
            f"/api/v1/media-versions/{media_version_id}/proxy",
            headers={"Range": "bytes=0-127"},
        )
        head = client.head(f"/api/v1/media-versions/{media_version_id}/proxy")
    assert ready.status_code == 206
    assert ready.headers["content-range"].startswith("bytes 0-127/")
    assert ready.headers["content-type"].startswith("video/mp4")
    assert len(ready.content) == 128
    assert head.status_code == 200
    assert head.headers["accept-ranges"] == "bytes"


def test_project_derivative_backfill_is_bounded_and_idempotent(workspace, database) -> None:
    project = _project(workspace, database)
    service = MediaService(database, workspace)
    for index, color in enumerate(("red", "blue")):
        source = workspace.work_root / f"historical-{index}.mp4"
        subprocess.run(
            [workspace.ffmpeg_path, "-f", "lavfi", "-i", f"color=c={color}:s=160x90:d=1", "-pix_fmt", "yuv420p", "-an", "-y", str(source)],
            check=True,
            capture_output=True,
        )
        service.import_file(str(project["id"]), source, schedule_derivatives=False)

    with TestClient(create_app(workspace)) as client:
        first = client.post(
            f"/api/v1/projects/{project['id']}/media-derivatives:backfill",
            params={"cursor": 0, "limit": 1},
        )
        replay = client.post(
            f"/api/v1/projects/{project['id']}/media-derivatives:backfill",
            params={"cursor": 0, "limit": 1},
        )
        second = client.post(
            f"/api/v1/projects/{project['id']}/media-derivatives:backfill",
            params={"cursor": 1, "limit": 1},
        )
    assert first.status_code == replay.status_code == second.status_code == 202
    assert first.json()["backfill"]["scanned"] == 1
    assert len(first.json()["backfill"]["jobs"]) == 4
    assert first.json()["backfill"]["submitted"] == 4
    assert first.json()["backfill"]["replayed"] == 0
    assert first.json()["backfill"]["has_more"] is True
    assert first.json()["backfill"]["next_cursor"] == 1
    assert replay.json()["backfill"]["submitted"] == 0
    assert replay.json()["backfill"]["replayed"] == 4
    assert second.json()["backfill"]["submitted"] == 4
    assert second.json()["backfill"]["has_more"] is False

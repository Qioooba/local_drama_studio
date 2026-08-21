from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
from local_drama.main import create_app


def test_episode_render_thumbnail_is_derived_cached_and_never_the_video(workspace, database, monkeypatch) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="episode_render_poster",
        title="Episode render poster",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1000,
        allow_unconfigured_capabilities=True,
    )
    season = ProjectService(database, workspace.projects_root).list_seasons(str(project["id"]))[0]
    episode = ProjectService(database, workspace.projects_root).list_episodes(str(season["id"]))[0]
    timeline = TimelineService(database, workspace).create_timeline_revision(
        str(episode["id"]),
        items=[{"start_us": 0, "end_us": 1_000_000, "track_type": "AUDIO"}],
        input_snapshot={"source": "thumbnail-test"},
    )
    project_root = workspace.projects_root / str(project["root_rel"])
    render_path = project_root / "05_outputs" / "episode-poster-source.mp4"
    render_path.parent.mkdir(parents=True, exist_ok=True)
    video_bytes = b"immutable-video-source-not-a-poster"
    render_path.write_bytes(video_bytes)
    render_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO episode_render_versions
            (id, episode_id, timeline_revision_id, rel_path, sha256, probe_json, integrity_status, duration_ms, mime_type,
             input_snapshot_json, ffmpeg_command_json, execution_log_text,
             created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, ?, ?, ?, '{}', 'VERIFIED', 1000, 'video/mp4', '{}', '{}', '', ?, ?, 'test', 1, 'v2')""",
            (
                render_id,
                str(episode["id"]),
                str(timeline["id"]),
                render_path.relative_to(project_root).as_posix(),
                hashlib.sha256(video_bytes).hexdigest(),
                now,
                now,
            ),
        )

    invocations: list[list[str]] = []

    def derive_webp(self: MediaService, args: list[str]) -> None:
        invocations.append(args)
        Path(args[-1]).write_bytes(b"derived-webp-poster")

    monkeypatch.setattr(MediaService, "_run_ffmpeg", derive_webp)
    with TestClient(create_app(workspace)) as client:
        url = f"/api/v1/episode-renders/{render_id}/thumbnail?size=medium&frame=poster"
        first = client.get(url)
        second = client.get(url)
        invalid = client.get(f"/api/v1/episode-renders/{render_id}/thumbnail?size=original")

    assert first.status_code == 200
    assert first.headers["content-type"].startswith("image/webp")
    # API middleware keeps sensitive local media non-cacheable in browsers;
    # derivation itself is still cached by immutable source hash server-side.
    assert first.headers["cache-control"] == "no-store"
    assert first.content == b"derived-webp-poster"
    assert first.content != video_bytes
    assert second.content == first.content
    assert len(invocations) == 1
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "THUMBNAIL_SIZE_UNSUPPORTED"

from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService
from local_drama.application.timeline import TimelineService
from local_drama.application.timeline_status import TimelineStatusService
from local_drama.main import create_app


def test_stale_timeline_refresh_rechecks_plan_and_creates_new_frozen_revision(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="timeline_refresh",
        title="Timeline refresh",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=2_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    episode_id = str(episode["id"])
    shot = projects.create_shot(episode_id, "S001", 2_000)

    source = workspace.work_root / "timeline-refresh.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=navy:s=160x90:d=2", "-pix_fmt", "yuv420p", "-an", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    media = MediaService(database, workspace).import_file(
        project_id,
        source,
        purpose="SHOT_VIDEO",
        owner_type="SHOT",
        owner_id=str(shot["id"]),
        media_kind="VIDEO",
        stage="PROXY",
    )
    media_id = str(media["media_version_id"])
    ReviewService(database, workspace).select_version(media_id, "PROXY_WINNER")
    original = TimelineService(database, workspace).create_timeline_revision(
        episode_id,
        [{"track_type": "VIDEO", "media_version_id": media_id, "start_us": 0, "end_us": 1_000_000, "parameters": {"shot_id": str(shot["id"]), "shot_code": "S001"}}],
        {"schema_version": "legacy-fixture"},
        status="FROZEN",
    )
    with database.transaction() as connection:
        connection.execute("UPDATE timeline_revisions SET status='STALE' WHERE id=?", (original["id"],))

    before = TimelineStatusService(database).inspect(episode_id)
    assert before["timeline"]["latest"]["status"] == "STALE"
    assert before["timeline"]["latest_frozen"] is None

    with TestClient(create_app(workspace)) as client:
        planned = client.get(f"/api/v1/episodes/{episode_id}/timeline-refresh:plan")
        assert planned.status_code == 200, planned.text
        plan = planned.json()["plan"]
        assert plan["status"] == "READY"
        assert plan["summary"] == {"shot_count": 1, "video_count": 1, "audio_count": 0, "subtitle_count": 0, "duration_us": 2_000_000}
        assert plan["would_create_status"] == "FROZEN"
        assert plan["requires_confirmation"] is True
        assert plan["mutated"] is False

        wrong = client.post(
            f"/api/v1/episodes/{episode_id}/timeline-refresh:commit",
            json={"expected_plan_hash": "0" * 64},
        )
        assert wrong.status_code == 409
        assert wrong.json()["error"]["code"] == "TIMELINE_REFRESH_PLAN_STALE"

        committed = client.post(
            f"/api/v1/episodes/{episode_id}/timeline-refresh:commit",
            json={"expected_plan_hash": plan["plan_hash"]},
        )
        assert committed.status_code == 201, committed.text
        timeline = committed.json()["timeline"]
        assert timeline["status"] == "FROZEN"
        assert timeline["revision_no"] == 2
        assert timeline["input_snapshot"]["source"] == "DELIVERY_STALE_REFRESH"
        assert timeline["input_snapshot"]["refreshed_from_timeline_revision_id"] == original["id"]
        assert timeline["items"][0]["end_us"] == 2_000_000

    after = TimelineStatusService(database).inspect(episode_id)
    assert after["timeline"]["latest_frozen"]["id"] == timeline["id"]
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='TIMELINE_STALE_REFRESH_COMMITTED' AND subject_id=?", (timeline["id"],)).fetchone()[0] == 1

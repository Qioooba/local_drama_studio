from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService
from local_drama.main import create_app


def _fixture(workspace, database):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="edit_v2", title="Edit v2", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 1_000)
    source = workspace.work_root / "edit-v2.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=purple:s=160x90:d=1", "-pix_fmt", "yuv420p", "-an", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    media = MediaService(database, workspace).import_file(
        str(project["id"]), source, purpose="SHOT_VIDEO", owner_type="SHOT",
        owner_id=str(shot["id"]), media_kind="VIDEO", stage="FORMAL",
    )
    ReviewService(database, workspace).select_version(str(media["media_version_id"]), "FORMAL_SELECTION")
    return project, episode, shot, media


def _draft_payload(workspace_fact: dict[str, object], *, key: str) -> dict[str, object]:
    clips = workspace_fact["video_clips"]
    return {
        "clips": [
            {
                "shot_id": item["shot_id"],
                "media_version_id": item["media_version_id"],
                "duration_us": item["end_us"] - item["start_us"],
                "source_start_us": item["source_start_us"],
                "transition_in": item["transition_in"],
            }
            for item in clips
        ],
        "include_dialogue": True,
        "include_music_and_sfx": True,
        "include_subtitles": True,
        "expected_latest_revision_id": workspace_fact["latest_revision"]["id"] if workspace_fact["latest_revision"] else None,
        "expected_upstream_fingerprint": workspace_fact["upstream_fingerprint"],
        "idempotency_key": key,
    }


def test_edit_workspace_v2_creates_idempotent_draft_and_freezes_new_revision(workspace, database) -> None:
    project, episode, _shot, _media = _fixture(workspace, database)
    with TestClient(create_app(workspace)) as client:
        initial = client.get(f"/api/v2/episodes/{episode['id']}/post/edit")
        assert initial.status_code == 200, initial.text
        fact = initial.json()["workspace"]
        assert fact["freshness"] == "EMPTY"
        assert fact["allowed_actions"] == ["CREATE_DRAFT"]
        assert fact["video_clips"][0]["shot_code"] == "S001"

        payload = _draft_payload(fact, key="edit-draft-1")
        created = client.post(f"/api/v2/episodes/{episode['id']}/post/edit/timeline-drafts", json=payload)
        assert created.status_code == 201, created.text
        draft = created.json()["timeline"]
        assert draft["status"] == "DRAFT" and draft["revision_no"] == 1
        replay = client.post(f"/api/v2/episodes/{episode['id']}/post/edit/timeline-drafts", json=payload)
        assert replay.status_code == 201, replay.text
        assert replay.json()["timeline"]["idempotent_replay"] is True

        current = client.get(f"/api/v2/episodes/{episode['id']}/post/edit").json()["workspace"]
        assert current["freshness"] == "CURRENT"
        assert "FREEZE_LATEST_DRAFT" in current["allowed_actions"]
        frozen = client.post(
            f"/api/v2/post/edit/timeline-revisions/{draft['id']}:freeze",
            json={
                "expected_latest_revision_id": draft["id"],
                "expected_upstream_fingerprint": current["upstream_fingerprint"],
                "idempotency_key": "edit-freeze-1",
            },
        )
        assert frozen.status_code == 201, frozen.text
        assert frozen.json()["timeline"]["status"] == "FROZEN"
        assert frozen.json()["timeline"]["revision_no"] == 2
        assert client.get(f"/api/v2/episodes/{episode['id']}/post/edit").json()["workspace"]["latest_revision"]["status"] == "FROZEN"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE action IN ('TIMELINE_DRAFT_CREATED_V2','TIMELINE_FROZEN_V2')").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM outbox_events WHERE type='TimelineRevisionChanged' AND project_id=?", (project["id"],)).fetchone()[0] == 2


def test_edit_workspace_v2_rejects_stale_upstream_and_revision_conflicts(workspace, database) -> None:
    _project, episode, _shot, _media = _fixture(workspace, database)
    with TestClient(create_app(workspace)) as client:
        fact = client.get(f"/api/v2/episodes/{episode['id']}/post/edit").json()["workspace"]
        payload = _draft_payload(fact, key="edit-draft-conflict")
        payload["expected_upstream_fingerprint"] = "0" * 64
        stale = client.post(f"/api/v2/episodes/{episode['id']}/post/edit/timeline-drafts", json=payload)
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "TIMELINE_UPSTREAM_CONFLICT"

        payload = _draft_payload(fact, key="edit-draft-ok")
        created = client.post(f"/api/v2/episodes/{episode['id']}/post/edit/timeline-drafts", json=payload)
        assert created.status_code == 201, created.text
        conflict_payload = {**payload, "idempotency_key": "edit-draft-late"}
        conflict = client.post(f"/api/v2/episodes/{episode['id']}/post/edit/timeline-drafts", json=conflict_payload)
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "TIMELINE_REVISION_CONFLICT"

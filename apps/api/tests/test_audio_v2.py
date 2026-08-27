from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def _fixture(workspace, database):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="audio_v2", title="Audio v2", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    source = workspace.work_root / "audio-v2.wav"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    media = MediaService(database, workspace).import_file(
        str(project["id"]), source, purpose="POST_AUDIO_SOURCE", owner_type="EPISODE",
        owner_id=str(episode["id"]), media_kind="AUDIO",
    )
    root = workspace.projects_root / str(project["root_rel"])
    evidence = root / "00_project" / "license-audio-v2.txt"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("user-owned test audio", encoding="utf-8")
    return project, episode, media, evidence.relative_to(root).as_posix()


def test_audio_workspace_v2_tracks_mix_revision_and_gaps(workspace, database) -> None:
    project, episode, media, evidence = _fixture(workspace, database)
    with TestClient(create_app(workspace)) as client:
        empty = client.get(f"/api/v2/episodes/{episode['id']}/post/audio")
        assert empty.status_code == 200, empty.text
        assert empty.json()["workspace"]["mix_revision"] == 0
        assert empty.json()["workspace"]["tracks"] == []

        payload = {
            "media_version_id": media["media_version_id"], "track_kind": "BGM",
            "start_us": 0, "end_us": 900_000, "gain_db": -4,
            "license_status": "USER_OWNED", "license_evidence_path_rel": evidence,
            "loop_enabled": False, "fade_in_us": 100_000, "fade_out_us": 100_000,
            "expected_mix_revision": 0, "idempotency_key": "audio-create-1",
        }
        created = client.post(f"/api/v2/episodes/{episode['id']}/post/audio/tracks", json=payload)
        assert created.status_code == 201, created.text
        track = created.json()["track"]
        assert track["outcome"] == "CREATED" and track["mix_revision"] == 1
        replay = client.post(f"/api/v2/episodes/{episode['id']}/post/audio/tracks", json=payload)
        assert replay.status_code == 201
        assert replay.json()["track"]["idempotent_replay"] is True

        workspace_response = client.get(f"/api/v2/episodes/{episode['id']}/post/audio").json()["workspace"]
        assert workspace_response["summary"]["bgm_count"] == 1
        assert workspace_response["tracks"][0]["authorization_status"] == "VERIFIED_EVIDENCE"
        assert workspace_response["tracks"][0]["allowed_actions"] == ["UPDATE_MIX_TRACK", "REMOVE_MIX_TRACK"]
        assert "license_evidence_path_rel" not in workspace_response["tracks"][0]
        assert workspace_response["project_id"] == project["id"]


def test_audio_track_v2_update_remove_are_revision_safe_idempotent_and_audited(workspace, database) -> None:
    _project, episode, media, evidence = _fixture(workspace, database)
    with TestClient(create_app(workspace)) as client:
        created = client.post(
            f"/api/v2/episodes/{episode['id']}/post/audio/tracks",
            json={
                "media_version_id": media["media_version_id"], "track_kind": "SFX",
                "start_us": 0, "end_us": 500_000, "gain_db": 0,
                "license_status": "USER_OWNED", "license_evidence_path_rel": evidence,
                "loop_enabled": False, "fade_in_us": 0, "fade_out_us": 0,
                "expected_mix_revision": 0, "idempotency_key": "audio-create-2",
            },
        ).json()["track"]
        updated = client.put(
            f"/api/v2/post/audio/tracks/{created['id']}",
            json={
                "start_us": 100_000, "end_us": 700_000, "gain_db": -2,
                "loop_enabled": False, "fade_in_us": 50_000, "fade_out_us": 50_000,
                "expected_revision": 1, "expected_mix_revision": 1, "idempotency_key": "audio-update-1",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["track"]["revision"] == 2
        stale = client.put(
            f"/api/v2/post/audio/tracks/{created['id']}",
            json={
                "start_us": 0, "end_us": 400_000, "gain_db": 0, "loop_enabled": False,
                "fade_in_us": 0, "fade_out_us": 0, "expected_revision": 1,
                "expected_mix_revision": 2, "idempotency_key": "audio-update-stale",
            },
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "AUDIO_TRACK_REVISION_CONFLICT"
        removed = client.post(
            f"/api/v2/post/audio/tracks/{created['id']}:remove",
            json={"expected_revision": 2, "expected_mix_revision": 2, "reason": "替换音效", "idempotency_key": "audio-remove-1"},
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["track"]["outcome"] == "REMOVED"
        assert client.get(f"/api/v2/episodes/{episode['id']}/post/audio").json()["workspace"]["mix_revision"] == 3
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE action LIKE 'AUDIO_TRACK_%'").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM outbox_events WHERE type IN ('AudioSourceChanged','MixDraftChanged')").fetchone()[0] == 3

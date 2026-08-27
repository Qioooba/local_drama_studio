from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.infrastructure.database.audio_repository import SqliteAudioWorkspaceRepository
from local_drama.main import create_app


def _fixture(workspace, database, code: str):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(code=code, title=code, episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=1000, allow_unconfigured_capabilities=True)
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    source = workspace.work_root / f"{code}.wav"
    subprocess.run([workspace.ffmpeg_path, "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-y", str(source)], check=True, capture_output=True)
    media = MediaService(database, workspace).import_file(str(project["id"]), source, purpose="AUDIO_BGM", owner_type="EPISODE", owner_id=str(episode["id"]), media_kind="AUDIO")
    root = workspace.projects_root / str(project["root_rel"])
    evidence = root / "00_admin" / f"{code}-license.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text('{"owner":"test"}', encoding="utf-8")
    return project, episode, media, evidence.relative_to(root).as_posix()


def _create(repository, episode, media, evidence):
    return repository.create_track(str(episode["id"]), {"media_version_id": str(media["media_version_id"]), "track_kind": "BGM", "start_us": 0, "end_us": 1_000_000, "gain_db": 0, "license_status": "USER_OWNED", "license_evidence_path_rel": evidence, "loop_enabled": False, "fade_in_us": 0, "fade_out_us": 0, "expected_mix_revision": 0, "idempotency_key": f"audio-fact:{episode['id']}"}, actor="test")


def test_audio_workspace_exposes_canonical_source_duration_and_authority(workspace, database) -> None:
    _project, episode, media, evidence = _fixture(workspace, database, "audio_facts")
    repository = SqliteAudioWorkspaceRepository(database, workspace)
    _create(repository, episode, media, evidence)
    items = repository.workspace(str(episode["id"]))["tracks"]
    assert len(items) == 1
    assert items[0]["source_name"] == "audio_facts.wav"
    assert items[0]["source_duration_ms"] == 1000
    assert items[0]["authorization_status"] == "VERIFIED_EVIDENCE"


def test_audio_remove_preserves_media_and_audit_history(workspace, database) -> None:
    _project, episode, media, evidence = _fixture(workspace, database, "audio_remove")
    repository = SqliteAudioWorkspaceRepository(database, workspace)
    binding = _create(repository, episode, media, evidence)
    with TestClient(create_app(workspace)) as client:
        response = client.post(f"/api/v2/post/audio/tracks/{binding['id']}:remove", json={"expected_revision": 1, "expected_mix_revision": 1, "reason": "test removal", "idempotency_key": "audio-remove-test"})
        assert response.status_code == 200, response.text
        assert response.json()["track"]["outcome"] == "REMOVED"
    assert repository.workspace(str(episode["id"]))["tracks"] == []
    assert MediaService(database, workspace).get_version(str(media["media_version_id"]))["id"] == str(media["media_version_id"])
    with database.connect() as connection:
        assert connection.execute("SELECT 1 FROM audit_events WHERE subject_id=? AND action='AUDIO_TRACK_REMOVED'", (binding["id"],)).fetchone() is not None

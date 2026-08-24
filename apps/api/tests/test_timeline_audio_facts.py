from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
from local_drama.main import create_app


def test_audio_binding_list_exposes_real_duration_source_and_waveform_cache_fact(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(code="audio_facts", title="Audio facts", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=1000, allow_unconfigured_capabilities=True)
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    source = workspace.work_root / "dialogue.wav"
    subprocess.run([workspace.ffmpeg_path, "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-y", str(source)], check=True, capture_output=True)
    media = MediaService(database, workspace).import_file(str(project["id"]), source, purpose="AUDIO_DIALOGUE", owner_type="EPISODE", owner_id=str(episode["id"]), media_kind="AUDIO")
    license_path = workspace.projects_root / str(project["root_rel"]) / "00_admin" / "dialogue-license.json"
    license_path.write_text('{"owner":"test"}', encoding="utf-8")
    TimelineService(database, workspace).bind_audio(str(episode["id"]), str(media["media_version_id"]), "DIALOGUE", 0, 1_000_000, license_evidence_path_rel="00_admin/dialogue-license.json")

    items = TimelineService(database, workspace).list_audio_bindings(str(episode["id"]))

    assert len(items) == 1
    assert items[0]["source_name"] == "dialogue.wav"
    assert items[0]["purpose"] == "AUDIO_DIALOGUE"
    assert items[0]["duration_ms"] == 1000
    assert items[0]["waveform_ready"] is False


def test_audio_unbind_preserves_media_and_audit_history(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(code="audio_unbind", title="Audio unbind", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=1000, allow_unconfigured_capabilities=True)
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    source = workspace.work_root / "bgm.wav"
    subprocess.run([workspace.ffmpeg_path, "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-y", str(source)], check=True, capture_output=True)
    media = MediaService(database, workspace).import_file(str(project["id"]), source, purpose="AUDIO_BGM", owner_type="EPISODE", owner_id=str(episode["id"]), media_kind="AUDIO")
    license_path = workspace.projects_root / str(project["root_rel"]) / "00_admin" / "bgm-license.json"
    license_path.write_text('{"owner":"test"}', encoding="utf-8")
    binding = TimelineService(database, workspace).bind_audio(str(episode["id"]), str(media["media_version_id"]), "BGM", 0, 1_000_000, license_evidence_path_rel="00_admin/bgm-license.json")
    binding_id = str(binding["id"])

    # Verify binding exists
    items = TimelineService(database, workspace).list_audio_bindings(str(episode["id"]))
    assert len(items) == 1

    # Test unbind via DELETE endpoint
    app = create_app(workspace)
    with TestClient(app) as client:
        bootstrap = client.get("/api/v1/session/bootstrap").json()
        token = bootstrap.get("token")
        response = client.delete(
            f"/api/v1/audio-bindings/{binding_id}",
            headers={"X-Local-Instance-Token": token},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["result"]["id"] == binding_id
        assert data["result"]["status"] == "UNBOUND"
        assert data["result"]["media_version_id"] == str(media["media_version_id"])

    # Verify binding is removed from episode
    items_after = TimelineService(database, workspace).list_audio_bindings(str(episode["id"]))
    assert len(items_after) == 0

    # Verify media version still exists
    media_info = MediaService(database, workspace).get_version(str(media["media_version_id"]))
    assert media_info["id"] == str(media["media_version_id"])

    # Verify audit event recorded
    with database.connect() as connection:
        audit = connection.execute(
            "SELECT * FROM audit_events WHERE subject_id=? AND action='AUDIO_BINDING_REMOVED'",
            (binding_id,),
        ).fetchone()
        assert audit is not None


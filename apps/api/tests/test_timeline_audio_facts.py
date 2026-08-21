from __future__ import annotations

import subprocess

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService


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

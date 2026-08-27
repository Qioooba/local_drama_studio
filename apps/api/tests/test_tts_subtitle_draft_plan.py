from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from local_drama.application.dialogue import DialogueService
from local_drama.application.media import MediaService
from local_drama.application.documents import DocumentImportService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def test_selected_tts_builds_read_only_script_authorized_subtitle_draft(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="tts_subtitle_plan",
        title="TTS subtitle plan",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=8_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    season = ProjectService(database, workspace.projects_root).list_seasons(project_id)[0]
    episode = ProjectService(database, workspace.projects_root).list_episodes(str(season["id"]))[0]
    episode_id = str(episode["id"])
    shot = ProjectService(database, workspace.projects_root).create_shot(episode_id, "S001", 8_000)

    script_path = workspace.work_root / "subtitle-plan-script.txt"
    script_path.write_text("甲：你好，世界。\n乙：今天继续拍摄。", encoding="utf-8")
    source = DocumentImportService(database, workspace).import_document(project_id, script_path)

    project_root = workspace.projects_root / str(project["root_rel"])
    license_path = project_root / "00_admin" / "voice-license.json"
    license_path.write_text('{"owner":"fixture"}\n', encoding="utf-8")
    audio_path = workspace.work_root / "subtitle-plan-audio.wav"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-y", str(audio_path)],
        check=True,
        capture_output=True,
    )
    media = MediaService(database, workspace).import_file(project_id, audio_path, purpose="DIALOGUE_TTS", media_kind="AUDIO")
    dialogue = DialogueService(database, workspace, media=MediaService(database, workspace))
    line = dialogue.create_line(episode_id, code="DLG-001", speaker="甲", text="你好，世界。", pronunciation={}, shot_id=str(shot["id"]))
    voice = dialogue.create_voice_profile(
        project_id,
        code="VOICE_FIXTURE",
        title="Fixture voice",
        voice_ref="local:fixture",
        license_status="USER_OWNED",
        license_evidence_path_rel="00_admin/voice-license.json",
    )
    candidate = dialogue.register_candidate(
        str(line["text_revisions"][-1]["id"]),
        voice_profile_version_id=str(voice["id"]),
        media_version_id=str(media["media_version_id"]),
        emotion="NEUTRAL",
        speech_rate=1.0,
        seed=7,
        model_ref="IMPORTED_LOCAL_AUDIO",
        candidate_kind="PREVIEW",
    )
    dialogue.select_candidate(str(candidate["id"]))

    with TestClient(create_app(workspace)) as client:
        response = client.get(
            f"/api/v1/episodes/{episode_id}/subtitle-draft-plan",
            params={"source_document_version_id": source["source_document_version_id"]},
        )
        assert response.status_code == 200, response.text
        plan = response.json()["plan"]
        assert plan["status"] == "READY"
        assert plan["ready_to_load"] is True
        assert plan["cues"] == [{"start_us": 0, "end_us": 3_000_000, "text": "你好，世界。"}]
        assert plan["evidence"][0]["text_revision_id"] == line["text_revisions"][-1]["id"]
        assert plan["evidence"][0]["tts_candidate_id"] == candidate["id"]
        assert plan["text_authority"] == "SCRIPT"
        assert plan["timing_authority"] == "SELECTED_TTS_MEDIA"
        assert plan["would_create_revision"] is False
        assert plan["mutated"] is False
        with database.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM subtitle_revisions WHERE episode_id=?", (episode_id,)).fetchone()[0] == 0

        dialogue.revise_text(str(line["id"]), expected_revision_no=1, text="文本已经改动。", pronunciation={})
        stale = client.get(
            f"/api/v1/episodes/{episode_id}/subtitle-draft-plan",
            params={"source_document_version_id": source["source_document_version_id"]},
        ).json()["plan"]
        assert stale["status"] == "BLOCKED"
        assert stale["missing"][0]["reason"] == "TTS_SELECTION_STALE"
        assert stale["cues"] == []

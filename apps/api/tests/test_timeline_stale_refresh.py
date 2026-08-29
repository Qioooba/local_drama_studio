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
        assert plan["summary"] == {"shot_count": 1, "video_count": 1, "audio_count": 0, "dialogue_count": 0, "subtitle_count": 0, "duration_us": 2_000_000}
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


def test_assemble_episode_timeline_creates_skips_then_refreshes(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="timeline_auto_assemble",
        title="Timeline auto assemble",
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

    source = workspace.work_root / "timeline-auto-assemble.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=teal:s=160x90:d=2", "-pix_fmt", "yuv420p", "-an", "-y", str(source)],
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
    ReviewService(database, workspace).select_version(str(media["media_version_id"]), "PROXY_WINNER")
    service = TimelineService(database, workspace)

    created = service.assemble_episode_timeline(episode_id, actor="automation-run")
    assert created["status"] == "CREATED"
    assert created["timeline"]["status"] == "FROZEN"
    assert created["timeline"]["input_snapshot"]["source"] == "AUTOMATION_RUN_ASSEMBLY"
    assert created["timeline"]["items"][0]["end_us"] == 2_000_000
    first_revision_id = str(created["timeline"]["id"])

    skipped = service.assemble_episode_timeline(episode_id, actor="automation-run")
    assert skipped["status"] == "SKIPPED"
    assert skipped["reason"] == "TIMELINE_ALREADY_CURRENT"
    assert str(skipped["timeline_revision_id"]) == first_revision_id
    assert skipped["mutated"] is False

    with database.transaction() as connection:
        connection.execute("UPDATE timeline_revisions SET status='STALE' WHERE id=?", (first_revision_id,))
    refreshed = service.assemble_episode_timeline(episode_id, actor="automation-run")
    assert refreshed["status"] == "CREATED"
    assert refreshed["timeline"]["revision_no"] == 2
    assert str(refreshed["source_timeline_revision_id"]) == first_revision_id

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE action='TIMELINE_AUTO_ASSEMBLED' AND subject_id=?", (str(refreshed["timeline"]["id"]),)).fetchone()[0] == 1


def test_assemble_episode_timeline_places_selected_dialogue_audio(workspace, database) -> None:
    import uuid as _uuid

    from local_drama.application.dialogue import DialogueService

    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="timeline_dialogue_assemble",
        title="Timeline dialogue assemble",
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

    video_source = workspace.work_root / "dialogue-assemble-shot.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=orange:s=160x90:d=2", "-pix_fmt", "yuv420p", "-an", "-y", str(video_source)],
        check=True,
        capture_output=True,
    )
    media_service = MediaService(database, workspace)
    video_media = media_service.import_file(
        project_id, video_source,
        purpose="SHOT_VIDEO", owner_type="SHOT", owner_id=str(shot["id"]), media_kind="VIDEO", stage="PROXY",
    )
    ReviewService(database, workspace).select_version(str(video_media["media_version_id"]), "PROXY_WINNER")

    dialogue = DialogueService(database, workspace)
    line = dialogue.create_line(
        episode_id, code="L001", speaker="主角", text="警报还没解除，我们不能停。",
        pronunciation={}, shot_id=str(shot["id"]),
    )
    text_revision_id = str(line["text_revisions"][-1]["id"])

    audio_source = workspace.work_root / "dialogue-assemble-line.wav"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5", "-ar", "48000", "-ac", "1", "-y", str(audio_source)],
        check=True,
        capture_output=True,
    )
    audio_media = media_service.import_file(
        project_id, audio_source,
        purpose="DIALOGUE_TTS", owner_type="DIALOGUE_TEXT_REVISION", owner_id=text_revision_id, media_kind="AUDIO", stage="FORMAL",
    )
    project_root = workspace.projects_root / str(project["root_rel"])
    license_file = project_root / "voice-license.txt"
    license_file.write_text("test license evidence", encoding="utf-8")
    voice = dialogue.create_voice_profile(
        project_id, code="VOICE-MAIN", title="主角音色", voice_ref="sapi:Huihui",
        license_status="USER_OWNED", license_evidence_path_rel=license_file.relative_to(project_root).as_posix(),
    )
    voice_profile_version_id = str(voice["id"])

    candidate_id, selection_id = str(_uuid.uuid4()), str(_uuid.uuid4())
    now = "2026-08-29T00:00:00Z"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO tts_candidates (id,dialogue_text_revision_id,voice_profile_version_id,media_version_id,
            emotion,speech_rate,seed,model_ref,candidate_kind,provenance_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,NULL,?,'FORMAL',?,'READY',?,?,?,1,'v2')""",
            (candidate_id, text_revision_id, voice_profile_version_id, str(audio_media["media_version_id"]),
             "neutral", 1.0, "WINDOWS_SAPI_LOCAL", "{}", now, now, "test"),
        )
        connection.execute(
            """INSERT INTO dialogue_candidate_selections (id,dialogue_line_id,tts_candidate_id,source_text_revision_id,
            created_at,updated_at,created_by,revision,schema_version) VALUES (?,?,?,?,?,?,?,1,'v2')""",
            (selection_id, str(line["id"]), candidate_id, text_revision_id, now, now, "test"),
        )

    created = TimelineService(database, workspace).assemble_episode_timeline(episode_id, actor="automation-run")
    assert created["status"] == "CREATED"
    items = created["timeline"]["items"]
    dialogue_items = [item for item in items if item["track_type"] == "DIALOGUE"]
    assert len(dialogue_items) == 1
    assert dialogue_items[0]["media_version_id"] == str(audio_media["media_version_id"])
    assert dialogue_items[0]["start_us"] == 0
    assert dialogue_items[0]["parameters"]["dialogue_line_id"] == str(line["id"])
    assert created["timeline"]["input_snapshot"]["schema_version"] == "localdrama.timeline-editor.v3"


def test_split_aligned_cue_groups_words_and_scales_times() -> None:
    from local_drama.application.timeline import split_aligned_cue

    words = [
        {"text": "警报", "start_time": 0.0, "end_time": 0.5},
        {"text": "还没", "start_time": 0.5, "end_time": 1.0},
        {"text": "解除", "start_time": 1.0, "end_time": 1.6},
        {"text": "。", "start_time": 1.6, "end_time": 1.8},
        {"text": "不能", "start_time": 1.8, "end_time": 2.2},
        {"text": "停", "start_time": 2.2, "end_time": 2.5},
    ]
    cues = split_aligned_cue("警报还没解除。不能停", words, 1_000_000, 2_500_000)
    assert cues, "有效的词级对齐必须产出子字幕"
    assert "".join(cue["text"] for cue in cues) == "警报还没解除。不能停"
    assert cues[0]["start_us"] == 1_000_000
    assert cues[-1]["end_us"] <= 1_000_000 + 2_500_000
    assert all(cue["end_us"] > cue["start_us"] for cue in cues)
    assert all(cues[index]["start_us"] >= cues[index - 1]["end_us"] for index in range(1, len(cues)))
    assert len(cues) >= 2  # 句号触发断句

    assert split_aligned_cue("文本", [], 0, 1_000_000) == []
    assert split_aligned_cue("文本", [{"text": "文", "start_time": "bad", "end_time": 1.0}], 0, 1_000_000) == []


def test_subtitle_draft_uses_word_alignment_when_available(workspace, database) -> None:
    import uuid as _uuid

    from local_drama.application.dialogue import DialogueService

    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="subtitle_words", title="词级字幕", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=2_000, allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    episode_id = str(episode["id"])
    shot = projects.create_shot(episode_id, "S001", 2_000)
    dialogue = DialogueService(database, workspace)
    line = dialogue.create_line(episode_id, code="L001", speaker="主角", text="警报还没解除，我们不能停。", pronunciation={}, shot_id=str(shot["id"]))
    text_revision_id = str(line["text_revisions"][-1]["id"])

    audio_source = workspace.work_root / "words-line.wav"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5", "-ar", "48000", "-ac", "1", "-y", str(audio_source)],
        check=True, capture_output=True,
    )
    audio_version = str(MediaService(database, workspace).import_file(
        project_id, audio_source,
        purpose="DIALOGUE_TTS", owner_type="DIALOGUE_TEXT_REVISION", owner_id=text_revision_id, media_kind="AUDIO", stage="FORMAL",
    )["media_version_id"])
    project_root = workspace.projects_root / str(project["root_rel"])
    (project_root / "00_admin").mkdir(parents=True, exist_ok=True)
    (project_root / "00_admin" / "voice-license.json").write_text('{"owner":"test"}\n', encoding="utf-8")
    voice = dialogue.create_voice_profile(
        project_id, code="VOICE-HERO", title="主角音色", voice_ref="sapi:Huihui",
        license_status="USER_OWNED", license_evidence_path_rel="00_admin/voice-license.json",
    )
    now = "2026-08-29T00:00:00Z"
    with database.transaction() as connection:
        candidate_id, selection_id = str(_uuid.uuid4()), str(_uuid.uuid4())
        connection.execute(
            """INSERT INTO tts_candidates (id,dialogue_text_revision_id,voice_profile_version_id,media_version_id,
            emotion,speech_rate,seed,model_ref,candidate_kind,provenance_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,NULL,?,'FORMAL','{}','READY',?,?,?,1,'v2')""",
            (candidate_id, text_revision_id, str(voice["id"]), audio_version, "neutral", 1.0, "WINDOWS_SAPI_LOCAL", now, now, "test"),
        )
        connection.execute(
            """INSERT INTO dialogue_candidate_selections (id,dialogue_line_id,tts_candidate_id,source_text_revision_id,
            created_at,updated_at,created_by,revision,schema_version) VALUES (?,?,?,?,?,?,?,1,'v2')""",
            (selection_id, str(line["id"]), candidate_id, text_revision_id, now, now, "test"),
        )

    service = TimelineService(database, workspace)
    words = [
        {"text": "警报", "start_time": 0.0, "end_time": 0.4},
        {"text": "还没", "start_time": 0.4, "end_time": 0.7},
        {"text": "解除", "start_time": 0.7, "end_time": 1.0},
        {"text": "，", "start_time": 1.0, "end_time": 1.05},
        {"text": "我们不能", "start_time": 1.05, "end_time": 1.3},
        {"text": "停", "start_time": 1.3, "end_time": 1.5},
    ]
    service.build_tts_aligner = lambda: (lambda audio_path, transcript: words)  # type: ignore[method-assign]
    draft = service.plan_tts_subtitle_draft(episode_id, align_words=True)
    assert draft["summary"]["aligned_lines"] == 1
    assert [cue for cue in draft["cues"] if cue["text"].startswith("警报")], draft["cues"]
    assert all(cue["start_us"] >= 0 for cue in draft["cues"])
    line_evidence = draft["evidence"][0]
    assert line_evidence["timing_basis"] == "FORCED_ALIGNMENT"

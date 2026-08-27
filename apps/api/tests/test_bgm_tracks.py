"""P1-11 BGM/SFX tracks: track_type policy + real-ffmpeg audio mixing in render_episode."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from local_drama.application.compose import ComposeService
from local_drama.application.documents import DocumentImportService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
from local_drama.application.worker import LocalMediaWorker
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.audio_repository import SqliteAudioWorkspaceRepository


def _video(workspace, name: str, seconds: float = 1.0) -> Path:
    output = workspace.work_root / name
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", f"color=c=navy:s=160x90:d={seconds}", "-pix_fmt", "yuv420p", "-an", "-y", str(output)],
        check=True,
        capture_output=True,
    )
    return output


def _audio(workspace, name: str, seconds: float = 1.0, frequency: int = 440) -> Path:
    output = workspace.work_root / name
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={seconds}", "-y", str(output)],
        check=True,
        capture_output=True,
    )
    return output


def _project_and_episode(workspace, database) -> tuple[dict[str, object], dict[str, object]]:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="bgm_tracks",
        title="BGM tracks",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1000,
        allow_unconfigured_capabilities=True,
    )
    project_service = ProjectService(database, workspace.projects_root)
    season = project_service.list_seasons(str(project["id"]))[0]
    episode = project_service.list_episodes(str(season["id"]))[0]
    return project, episode


def _license_evidence(project_root: Path) -> Path:
    path = project_root / "00_admin" / "audio-license.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"owner":"test-operator","scope":"bgm fixture"}\n', encoding="utf-8", newline="")
    return path


def _bind(workspace, database, project_root: Path, episode_id: str, audio_media_version_id: str, track_type: str, **kwargs) -> dict[str, object]:
    repository = SqliteAudioWorkspaceRepository(database, workspace)
    mix_revision = repository.workspace(episode_id)["mix_revision"]
    result = repository.create_track(episode_id, {
        "media_version_id": audio_media_version_id, "track_kind": track_type,
        "start_us": kwargs.get("start_us", 0), "end_us": kwargs.get("end_us", 1_000_000),
        "gain_db": kwargs.get("gain_db", 0.0), "license_status": "VERIFIED_LOCAL",
        "license_evidence_path_rel": str(_license_evidence(project_root).relative_to(project_root).as_posix()),
        "loop_enabled": kwargs.get("loop_enabled", False), "fade_in_us": kwargs.get("fade_in_us", 0),
        "fade_out_us": kwargs.get("fade_out_us", 0), "expected_mix_revision": mix_revision,
        "idempotency_key": f"bgm-test:{track_type}:{mix_revision}",
    }, actor="test")
    return {**result, "track_type": result["track_kind"]}


def test_track_type_validation_accepts_only_canonical_post_mix_kinds(workspace, database) -> None:
    project, episode = _project_and_episode(workspace, database)
    project_root = workspace.projects_root / str(project["root_rel"])
    audio = MediaService(database, workspace).import_file(str(project["id"]), _audio(workspace, "bgm.wav"), purpose="AUDIO", media_kind="AUDIO")

    canonical = _bind(workspace, database, project_root, str(episode["id"]), str(audio["media_version_id"]), "BGM")
    assert canonical["track_type"] == "BGM"
    sfx = _bind(workspace, database, project_root, str(episode["id"]), str(audio["media_version_id"]), "SFX")
    assert sfx["track_type"] == "SFX"
    for unsupported in ("DIALOGUE", "MUSIC", "ENVIRONMENT", "FOO"):
        with pytest.raises(DomainRuleError) as error:
            _bind(workspace, database, project_root, str(episode["id"]), str(audio["media_version_id"]), unsupported)
        assert error.value.code == "AUDIO_TRACK_KIND_UNSUPPORTED"


def test_render_mixes_bgm_audio_stream_with_real_ffmpeg(workspace, database) -> None:
    project, episode = _project_and_episode(workspace, database)
    project_root = workspace.projects_root / str(project["root_rel"])
    media = MediaService(database, workspace)
    video = media.import_file(str(project["id"]), _video(workspace, "bgm-video.mp4"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    bgm = media.import_file(str(project["id"]), _audio(workspace, "bgm-track.wav"), purpose="AUDIO", media_kind="AUDIO")
    _bind(workspace, database, project_root, str(episode["id"]), str(bgm["media_version_id"]), "BGM", gain_db=-6.0, loop_enabled=True, fade_in_us=50_000, fade_out_us=50_000)

    timeline = TimelineService(database, workspace).create_timeline_revision(
        str(episode["id"]),
        [{"track_type": "VIDEO", "media_version_id": str(video["media_version_id"]), "start_us": 0, "end_us": 1_000_000, "parameters": {}}],
        {"source": "bgm-test"},
    )
    render = TimelineService(database, workspace).render_episode(str(timeline["id"]))
    assert render["status"] == "VERIFIED"
    assert render["input_snapshot"]["render_mode"] == "MIXED_AUDIO"
    bindings = render["input_snapshot"]["audio_bindings"]
    assert len(bindings) == 1
    assert bindings[0]["track_type"] == "BGM"
    assert bindings[0]["loop_enabled"] is True
    assert bindings[0]["gain_db"] == -6.0

    streams = render["probe"]["streams"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    assert audio_streams, "渲染产物必须包含混音后的音频流"
    assert audio_streams[0]["codec_name"] == "aac"
    assert 900 <= render["probe"]["duration_ms"] <= 1100
    render_path = project_root / render["rel_path"]
    assert render_path.is_file()
    # Execution log records timeline-duration normalization and all mix stages.
    log = json.loads(render["execution_log"])
    assert [step["stage"] for step in log["steps"]] == ["timeline-duration", "concat", "mix", "mux"]


def test_render_without_bindings_honors_timeline_duration_contract(workspace, database) -> None:
    project, episode = _project_and_episode(workspace, database)
    project_root = workspace.projects_root / str(project["root_rel"])
    video = MediaService(database, workspace).import_file(str(project["id"]), _video(workspace, "plain-video.mp4"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    timeline = TimelineService(database, workspace).create_timeline_revision(
        str(episode["id"]),
        [{"track_type": "VIDEO", "media_version_id": str(video["media_version_id"]), "start_us": 0, "end_us": 1_000_000, "parameters": {}}],
        {"source": "no-bindings-test"},
    )
    service = TimelineService(database, workspace)
    render = service.render_episode(str(timeline["id"]))
    assert render["status"] == "VERIFIED"
    assert "render_mode" not in render["input_snapshot"]
    # Timeline items are normalized before concat even when no extra audio is bound.
    log = json.loads(render["execution_log"])
    assert [step["stage"] for step in log["steps"]] == ["timeline-duration", "concat"]
    assert render["ffmpeg_command"]["args"][:2] == ["-f", "concat"]
    assert "-c:a" in render["ffmpeg_command"]["args"] and "aac" in render["ffmpeg_command"]["args"]
    assert (project_root / render["rel_path"]).is_file()

    replay = service.render_episode(str(timeline["id"]))
    assert replay["id"] == render["id"]
    assert replay["idempotent_replay"] is True
    assert replay["compose_fingerprint"] == render["compose_fingerprint"]
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM episode_render_versions WHERE timeline_revision_id=?", (timeline["id"],)).fetchone()[0] == 1

    forced = service.render_episode(str(timeline["id"]), force_rerender=True)
    assert forced["id"] != render["id"]
    assert "idempotent_replay" not in forced
    with database.transaction() as connection:
        connection.execute("UPDATE timeline_revisions SET status='STALE' WHERE id=?", (timeline["id"],))
    with pytest.raises(DomainRuleError) as stale:
        service.render_episode(str(timeline["id"]))
    assert stale.value.code == "TIMELINE_STALE"


def test_render_extends_short_source_and_burns_selected_subtitle(workspace, database) -> None:
    project, episode = _project_and_episode(workspace, database)
    media = MediaService(database, workspace).import_file(
        str(project["id"]),
        _video(workspace, "short-subtitle-source.mp4", seconds=0.35),
        purpose="SHOT_VIDEO",
        media_kind="VIDEO",
    )
    script_path = workspace.work_root / "subtitle-render-script.txt"
    script_path.write_text("字幕验收", encoding="utf-8")
    script = DocumentImportService(database, workspace).import_document(str(project["id"]), script_path)
    service = TimelineService(database, workspace)
    subtitle = service.create_subtitle_revision(
        str(episode["id"]),
        [{"start_us": 0, "end_us": 1_000_000, "text": "字幕验收"}],
        format="ASS",
        authority={
            "text_authority": "SCRIPT",
            "source_document_version_id": script["source_document_version_id"],
        },
    )
    timeline = service.create_timeline_revision(
        str(episode["id"]),
        [
            {
                "track_type": "VIDEO",
                "media_version_id": str(media["media_version_id"]),
                "start_us": 0,
                "end_us": 1_200_000,
                "parameters": {},
            }
        ],
        {"source": "duration-subtitle-test", "subtitle_revision_id": subtitle["id"]},
    )

    render = service.render_episode(str(timeline["id"]))

    assert 1_150 <= render["probe"]["duration_ms"] <= 1_300
    assert render["input_snapshot"]["renderer_contract"] == "TIMELINE_DURATION_AND_SUBTITLE_V2"
    assert render["input_snapshot"]["subtitle_revision"] == {
        "id": subtitle["id"],
        "revision_no": subtitle["revision_no"],
        "format": "ASS",
        "content_hash": subtitle["content_hash"],
        "status": "DRAFT",
    }
    stages = [step["stage"] for step in json.loads(render["execution_log"])["steps"]]
    assert stages == ["timeline-duration", "concat", "subtitle"]
    assert any("subtitles=filename=" in arg for arg in render["ffmpeg_command"]["args"])


def test_compose_uses_durable_job_and_reuses_running_and_completed_fingerprint(workspace, database) -> None:
    project, episode = _project_and_episode(workspace, database)
    video = MediaService(database, workspace).import_file(
        str(project["id"]), _video(workspace, "compose-job.mp4"), purpose="SHOT_VIDEO", media_kind="VIDEO",
    )
    timeline = TimelineService(database, workspace).create_timeline_revision(
        str(episode["id"]),
        [{"track_type": "VIDEO", "media_version_id": str(video["media_version_id"]), "start_us": 0, "end_us": 1_000_000, "parameters": {}}],
        {"source": "compose-job-test"},
    )
    compose = ComposeService(database, workspace)
    preflight = compose.preflight(str(timeline["id"]))
    assert preflight["would_execute_ffmpeg"] is True
    assert preflight["writes_performed"] == 0
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM episode_render_versions").fetchone()[0] == 0

    first = compose.submit(str(timeline["id"]))
    replay = compose.submit(str(timeline["id"]))
    assert first["job"]["state"] == "QUEUED"
    assert replay["job"]["id"] == first["job"]["id"]
    assert replay["idempotent_replay"] is True
    assert first["job"]["input_snapshot"]["compose_fingerprint"] == preflight["compose_fingerprint"]
    with database.transaction() as connection:
        connection.execute("UPDATE jobs SET state='RUNNING' WHERE id=?", (first["job"]["id"],))
    running = compose.submit(str(timeline["id"]))
    assert running["job"]["id"] == first["job"]["id"]
    assert running["job"]["state"] == "RUNNING"
    with database.transaction() as connection:
        connection.execute("UPDATE jobs SET state='QUEUED' WHERE id=?", (first["job"]["id"],))
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM episode_render_versions").fetchone()[0] == 0

    outcome = LocalMediaWorker(database, workspace).run_once("compose-worker", ["CPU"])
    assert outcome is not None and outcome["job"]["id"] == first["job"]["id"]
    assert outcome["result"]["job_state"] == "SUCCEEDED"
    assert outcome["artifact"]["kind"] == "EPISODE_COMPOSE_REPORT"
    completed = compose.submit(str(timeline["id"]))
    assert completed["job"] is None
    assert completed["render"]["idempotent_replay"] is True
    assert completed["render"]["compose_fingerprint"] == preflight["compose_fingerprint"]
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM episode_render_versions").fetchone()[0] == 1

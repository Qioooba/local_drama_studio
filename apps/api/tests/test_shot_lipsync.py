"""LatentSync lip sync lane: job creation, worker execution, finalize promotion."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from local_drama.application.jobs import JobService
from local_drama.application.lipsync import LipsyncService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.worker import LocalMediaWorker
from local_drama.domain.errors import DomainRuleError


class _FakeLipsyncRuntime:
    def __init__(self, workspace) -> None:
        self.workspace = workspace
        self.calls: list[dict] = []

    def run_lipsync(self, *, video_path: Path, audio_path: Path, output_path: Path, inference_steps: int = 20, timeout: float = 3600) -> None:
        self.calls.append({"video": str(video_path), "audio": str(audio_path)})
        output_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [self.workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=purple:s=160x90:d=1", "-pix_fmt", "yuv420p", "-y", str(output_path)],
            check=True,
            capture_output=True,
        )


def _seed_media(workspace, database, code: str) -> tuple[dict, str, str, str]:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code, title=code, episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=4_000, allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    media = MediaService(database, workspace)

    video_source = workspace.work_root / f"{code}-video.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=navy:s=160x90:d=2", "-pix_fmt", "yuv420p", "-an", "-y", str(video_source)],
        check=True, capture_output=True,
    )
    video_version = str(media.import_file(
        str(project["id"]), video_source,
        purpose="SHOT_VIDEO", owner_type="SHOT", owner_id=str(shot["id"]), media_kind="VIDEO", stage="PROXY",
    )["media_version_id"])

    audio_source = workspace.work_root / f"{code}-audio.wav"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0", "-ar", "48000", "-ac", "1", "-y", str(audio_source)],
        check=True, capture_output=True,
    )
    audio_version = str(media.import_file(
        str(project["id"]), audio_source,
        purpose="DIALOGUE_TTS", owner_type="SHOT", owner_id=str(shot["id"]), media_kind="AUDIO", stage="PROXY",
    )["media_version_id"])
    return project, str(shot["id"]), video_version, audio_version


def test_lipsync_job_create_execute_finalize(workspace, database) -> None:
    project, shot_id, video_version, audio_version = _seed_media(workspace, database, "lipsync_happy")
    service = LipsyncService(
        database, workspace,
        jobs=JobService(database, workspace),
        media=MediaService(database, workspace),
    )
    created = service.create_job(
        shot_id,
        video_media_version_id=video_version,
        audio_media_version_id=audio_version,
        idempotency_key="lipsync-1",
    )
    job = JobService(database, workspace).get_job(str(created["id"]))
    assert job["input_snapshot"]["video_media_version_id"] == video_version
    assert job["input_snapshot"]["audio_sha256"]
    assert job["input_snapshot"]["network_allowed"] is False

    worker = LocalMediaWorker(database, workspace)
    worker.voxcpm_runtime = _FakeLipsyncRuntime(workspace)
    worker.run_until_idle("lipsync-worker", max_jobs=20)

    result = service.finalize_job(str(created["id"]))
    assert result["media"]["media_kind"] == "VIDEO"
    assert result["media"]["purpose"] == "LIPSYNC"
    listing = service.list_shot_jobs(shot_id)
    assert listing["items"][0]["state"] == "SUCCEEDED"
    assert listing["items"][0]["output_media_version_id"] == str(result["media"]["id"])
    again = service.finalize_job(str(created["id"]))
    assert again["idempotent_replay"] is True


def test_lipsync_job_rejects_unknown_video_input(workspace, database) -> None:
    project, shot_id, _video_version, audio_version = _seed_media(workspace, database, "lipsync_bad")
    service = LipsyncService(
        database, workspace,
        jobs=JobService(database, workspace),
        media=MediaService(database, workspace),
    )
    with pytest.raises(DomainRuleError):
        service.create_job(
            shot_id,
            video_media_version_id="missing-video",
            audio_media_version_id=audio_version,
            idempotency_key="lipsync-bad-1",
        )

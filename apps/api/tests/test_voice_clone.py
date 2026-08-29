"""Zero-shot voice cloning: reference validation, profile creation, binding."""
from __future__ import annotations

import subprocess
import uuid
from datetime import UTC, datetime

import pytest

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.story_assets import StoryAssetService
from local_drama.application.voice_clone import VoiceCloneService
from local_drama.domain.errors import DomainRuleError


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _character(workspace, database, code: str) -> tuple[dict, dict]:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code, title=code, episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=4_000, allow_unconfigured_capabilities=True,
    )
    asset = StoryAssetService(database, workspace).create_asset(
        str(project["id"]), "CHARACTER", "HERO_01", "主角", "声线克隆验收",
    )
    return project, asset


def _audio(workspace, database, project_id: str, seconds: float) -> str:
    source = workspace.work_root / f"clone-ref-{uuid.uuid4().hex[:6]}.wav"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-ar", "48000", "-ac", "1", "-y", str(source)],
        check=True, capture_output=True,
    )
    return str(MediaService(database, workspace).import_file(
        project_id, source,
        purpose="AUDIO_REFERENCE", owner_type="PROJECT", owner_id=project_id, media_kind="AUDIO", stage="IMPORTED",
    )["media_version_id"])


def test_voice_clone_creates_voxcpm_voice_and_binds_character(workspace, database) -> None:
    project, asset = _character(workspace, database, "clone_happy")
    reference = _audio(workspace, database, str(project["id"]), 4.0)
    service = VoiceCloneService(database, workspace)

    result = service.clone_character_voice(
        str(project["id"]), str(asset["id"]),
        media_version_id=reference, title="主角·平静", transcript="警报还没解除。", consent=True,
    )
    voice = result["voice"]
    assert str(voice["voice_ref"]).startswith(f"voxcpm2:media:{reference}|")
    assert voice["license_status"] == "USER_OWNED"
    assert voice["provider_profile_version_id"] is None  # no published TTS profile in this bare project
    with database.connect() as connection:
        binding = connection.execute(
            "SELECT voice_profile_version_id FROM character_voice_bindings WHERE character_asset_id=?",
            (str(asset["id"]),),
        ).fetchone()
    assert str(binding["voice_profile_version_id"]) == str(voice["id"])


def test_voice_clone_rebinds_and_enforces_guards(workspace, database) -> None:
    project, asset = _character(workspace, database, "clone_guards")
    service = VoiceCloneService(database, workspace)
    first = _audio(workspace, database, str(project["id"]), 4.0)
    service.clone_character_voice(
        str(project["id"]), str(asset["id"]),
        media_version_id=first, title="第一版", transcript="", consent=True,
    )

    # Re-cloning replaces the single character binding.
    second = _audio(workspace, database, str(project["id"]), 5.0)
    service.clone_character_voice(
        str(project["id"]), str(asset["id"]),
        media_version_id=second, title="第二版", transcript="", consent=True,
    )
    with database.connect() as connection:
        bindings = connection.execute(
            "SELECT COUNT(*) FROM character_voice_bindings WHERE character_asset_id=?",
            (str(asset["id"]),),
        ).fetchone()[0]
    assert int(bindings) == 1

    # Guards: missing consent, out-of-range duration, non-character asset.
    with pytest.raises(DomainRuleError, match="授权"):
        service.clone_character_voice(
            str(project["id"]), str(asset["id"]),
            media_version_id=first, title="", transcript="", consent=False,
        )
    short = _audio(workspace, database, str(project["id"]), 1.0)
    with pytest.raises(DomainRuleError, match="时长"):
        service.clone_character_voice(
            str(project["id"]), str(asset["id"]),
            media_version_id=short, title="", transcript="", consent=True,
        )

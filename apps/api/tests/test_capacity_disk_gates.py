from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from local_drama.application.compose import ComposeService
from local_drama.application.episode_production_runs import EpisodeProductionRunService
from local_drama.application.generation import GenerationService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
from local_drama.domain.errors import DomainRuleError
from tests.test_bgm_tracks import _video
from tests.test_generation_variants import _image, _plan, _project, _published_profile


def _usage(free: int):
    return SimpleNamespace(total=free + 10_000, used=10_000, free=free)


def test_single_generation_declared_disk_gate_rechecks_before_submit_and_writes_nothing(workspace, database, monkeypatch) -> None:
    project = _project(workspace, database, "generation_disk_gate")
    project_id = str(project["id"])
    media_version_id = _image(workspace, database, project_id, "generation-disk.png")
    profile_id = _published_profile(workspace, database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET resource_policy_json=?,revision=revision+1 WHERE id=?",
            (json.dumps({"estimated_disk_bytes_per_take": 50_000}), profile_id),
        )
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "PROJECT", project_id, "I2V", "disk gate")
    plan = _plan(profile_id, media_version_id)
    monkeypatch.setattr("local_drama.application.generation.shutil.disk_usage", lambda path: _usage(1_000))

    preflight = generation.preflight_variant(str(intent["id"]), plan)
    assert preflight["status"] == "BLOCKED"
    assert preflight["disk_gate"] == {
        "status": "BLOCKED", "blocking": True, "code": "GENERATION_DISK_SPACE_LOW",
        "free_bytes": 1_000, "required_bytes": 50_000,
        "estimate_source": "FROZEN_PROFILE_RESOURCE_POLICY", "filesystem_source": "PROJECT_ROOT",
        "project_id": project_id,
    }
    with pytest.raises(DomainRuleError) as error:
        generation.submit_confirmed_variant(str(intent["id"]), plan, str(preflight["plan_hash"]), "disk-blocked-submit")
    assert error.value.code == "GENERATION_DISK_PREFLIGHT_BLOCKED"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_variants WHERE intent_id=?", (intent["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE project_id=?", (project_id,)).fetchone()[0] == 0


def test_generation_unknown_disk_estimate_is_honest_not_invented(workspace, database, monkeypatch) -> None:
    project = _project(workspace, database, "generation_disk_unknown")
    project_id = str(project["id"])
    media_version_id = _image(workspace, database, project_id, "generation-disk-unknown.png")
    profile_id = _published_profile(workspace, database)
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "PROJECT", project_id, "I2V", "unknown disk estimate")
    monkeypatch.setattr("local_drama.application.generation.shutil.disk_usage", lambda path: _usage(1))
    preflight = generation.preflight_variant(str(intent["id"]), _plan(profile_id, media_version_id))
    assert preflight["disk_gate"]["status"] == "ESTIMATE_UNAVAILABLE"
    assert preflight["disk_gate"]["required_bytes"] is None
    assert preflight["disk_gate"]["estimate_source"] == "UNKNOWN"


def test_compose_frozen_input_disk_gate_blocks_before_job_or_render(workspace, database, monkeypatch) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="compose_disk_gate", title="Compose disk gate", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=1_000, allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    video = MediaService(database, workspace).import_file(
        str(project["id"]), _video(workspace, "compose-disk.mp4"), purpose="SHOT_VIDEO", media_kind="VIDEO",
    )
    timeline = TimelineService(database, workspace).create_timeline_revision(
        str(episode["id"]),
        [{"track_type": "VIDEO", "media_version_id": str(video["media_version_id"]), "start_us": 0, "end_us": 1_000_000, "parameters": {}}],
        {"source": "disk-gate-test"},
    )
    monkeypatch.setattr("local_drama.application.compose.shutil.disk_usage", lambda path: _usage(1_024))
    compose = ComposeService(database, workspace)
    preflight = compose.preflight(str(timeline["id"]))
    assert preflight["status"] == "BLOCKED"
    assert preflight["disk_gate"]["free_bytes"] == 1_024
    assert preflight["disk_gate"]["required_bytes"] >= 64 * 1024 * 1024
    assert preflight["disk_gate"]["estimate_source"] == "FROZEN_COMPOSE_INPUT_SIZE_POLICY_V1"
    assert preflight["would_execute_ffmpeg"] is True
    with pytest.raises(DomainRuleError) as error:
        compose.submit(str(timeline["id"]))
    assert error.value.code == "COMPOSE_DISK_PREFLIGHT_BLOCKED"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE type='EPISODE_COMPOSE'").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM episode_render_versions WHERE timeline_revision_id=?", (timeline["id"],)).fetchone()[0] == 0


def test_episode_quality_gate_uses_shots_times_frozen_profile_take_estimate_and_zero_writes(workspace, database, monkeypatch) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="episode_disk_gate", title="Episode disk gate", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    projects.create_shot(str(episode["id"]), "S001", 4_000)
    profile_id = _published_profile(workspace, database)
    now = "2026-08-20T00:00:00Z"
    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET resource_policy_json=?,revision=revision+1 WHERE id=?",
            (json.dumps({"disk_bytes_per_take": 10_000}), profile_id),
        )
        connection.execute(
            """INSERT INTO project_profile_bindings
            (id,project_id,capability,execution_profile_version_id,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?, 'I2V_VIDEO',?,'ACTIVE',?,?, 'test',1,'v2')""",
            (str(uuid.uuid4()), project_id, profile_id, now, now),
        )
    monkeypatch.setattr("local_drama.application.episode_production_runs.shutil.disk_usage", lambda path: _usage(20_000))
    service = EpisodeProductionRunService(database, workspace)
    preflight = service.preflight(str(episode["id"]), tts_enabled=False, production_mode="QUALITY", min_free_disk_bytes=1)
    disk = next(item for item in preflight["checks"] if item["code"] == "DISK_SPACE_LOW")
    assert disk["status"] == "BLOCKED"
    assert disk["evidence"]["disk_bytes_per_take"] == 10_000
    assert disk["evidence"]["take_count"] == 4
    assert disk["evidence"]["estimated_output_bytes"] == 40_000
    assert disk["evidence"]["required_free_bytes"] == 40_000
    with database.connect() as connection:
        before = tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("automation_workflows", "automation_workflow_runs", "jobs"))
    with pytest.raises(DomainRuleError) as error:
        service.start(str(episode["id"]), idempotency_key="episode-disk-blocked", tts_enabled=False, production_mode="QUALITY", min_free_disk_bytes=1)
    assert error.value.code == "EPISODE_PRODUCTION_PREFLIGHT_BLOCKED"
    assert "DISK_SPACE_LOW" in error.value.details["blocker_codes"]
    with database.connect() as connection:
        after = tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("automation_workflows", "automation_workflow_runs", "jobs"))
    assert after == before

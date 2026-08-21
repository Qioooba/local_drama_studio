from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from local_drama.application.jobs import JobService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.application.queries.generation_estimates import GenerationEstimateService
from local_drama.infrastructure.database.generation_estimate_repository import SqliteGenerationEstimateRepository
from local_drama.main import create_app


def _project(workspace, database) -> str:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="estimate_test", title="Estimate test", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    return str(project["id"])


def _profile(workspace, database) -> str:
    service = ProfileService(database, workspace.manifest_path)
    service.sync_manifest()
    return str(service.list_profiles()[0]["version_id"])


def _attempt(workspace, database, project_id: str, profile_id: str, key: str, seconds: int, *, width: int = 1280, success: bool = True) -> None:
    service = JobService(database, workspace)
    service.create_job(
        project_id, "GENERATION_VARIANT", "PROJECT", project_id, "GPU_H3",
        {"semantic_inputs": {"width": width, "height": 720, "duration_seconds": 4, "frames": 97, "steps": 20}},
        key, execution_profile_version_id=profile_id, max_attempts=1,
    )
    claimed = service.claim(f"worker-{key}", ["GPU_H3"])
    assert claimed is not None
    attempt_id = str(claimed["attempt"]["id"])
    service.complete(attempt_id, str(claimed["attempt"]["lease_token"]), f"worker-{key}", success=success)
    start = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE job_attempts SET started_at=?,finished_at=? WHERE id=?",
            (start.isoformat(), (start + timedelta(seconds=seconds)).isoformat(), attempt_id),
        )


def test_estimate_uses_matching_successful_attempts_and_is_read_only(workspace, database) -> None:
    project_id = _project(workspace, database)
    profile_id = _profile(workspace, database)
    for index, seconds in enumerate((10, 20, 40)):
        _attempt(workspace, database, project_id, profile_id, f"match-{index}", seconds)
    _attempt(workspace, database, project_id, profile_id, "other-resolution", 300, width=1920)
    _attempt(workspace, database, project_id, profile_id, "failed", 999, success=False)

    with TestClient(create_app(workspace)) as client:
        # App startup may emit its own lifecycle audit. Establish the baseline
        # after startup so this assertion measures the GET itself.
        with database.connect() as connection:
            before = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        response = client.get(
            "/api/v1/generation-estimates",
            params={
                "profile_version_id": profile_id, "width": 1280, "height": 720,
                "duration_seconds": 4, "frame_count": 97, "steps": 20,
                "gpu_class": "GPU_H3_HEAVY",
            },
        )
        with database.connect() as connection:
            after = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        assert after == before
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "AVAILABLE"
    assert result["sample_count"] == 3
    assert result["p50_seconds"] == 20
    assert result["p90_seconds"] == 40
    assert result["dimensions"]["gpu_hardware_model"] is None
    assert result["dimensions"]["gpu_hardware_model_known"] is False
    assert result["audit"]["writes_performed"] == 0
    with database.connect() as connection:
        lease = connection.execute("SELECT resource_key,released_at FROM job_resource_leases ORDER BY created_at LIMIT 1").fetchone()
        assert lease["resource_key"] == "GPU_H3_HEAVY"
        assert lease["released_at"] is not None


def test_insufficient_history_never_returns_guessed_seconds(workspace, database) -> None:
    project_id = _project(workspace, database)
    profile_id = _profile(workspace, database)
    _attempt(workspace, database, project_id, profile_id, "only-one", 12)
    with database.connect() as connection:
        connection.execute("PRAGMA query_only=ON")
        result = GenerationEstimateService(SqliteGenerationEstimateRepository(connection)).estimate(
            profile_version_id=profile_id, width=1280, height=720, steps=20,
        )
    assert result["status"] == "NO_LOCAL_ESTIMATE"
    assert result["reason"] == "INSUFFICIENT_SAMPLES"
    assert result["sample_count"] == 1
    assert result["p50_seconds"] is None
    assert result["p90_seconds"] is None


def test_old_schema_fails_closed_without_writes(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY)")
    connection.execute("PRAGMA query_only=ON")
    result = GenerationEstimateService(SqliteGenerationEstimateRepository(connection)).estimate(profile_version_id="legacy-profile")
    assert result["status"] == "NO_LOCAL_ESTIMATE"
    assert result["reason"] == "SCHEMA_UNAVAILABLE"
    assert result["p50_seconds"] is None
    assert result["p90_seconds"] is None
    assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    connection.close()

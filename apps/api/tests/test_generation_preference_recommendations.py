from __future__ import annotations

import json
import sqlite3
from typing import Any

from local_drama.application.queries.generation_preferences import GenerationPreferenceQueryService
from local_drama.infrastructure.database.generation_preference_repository import SqliteGenerationPreferenceRepository


class FakeRepository:
    def __init__(self, attempts: list[dict[str, Any]], *, schema_available: bool = True) -> None:
        self.attempts = attempts
        self.schema_available = schema_available

    def project_exists(self, project_id: str) -> bool:
        return project_id == "project-1"

    def owner_project_id(self, owner_type: str, owner_id: str) -> str | None:
        return None

    def current_preference(self, project_id: str, owner_type: str, owner_id: str, capability: str) -> None:
        return None

    def auto_profile(self, capability: str) -> dict[str, Any]:
        return self.profile("profile-version-1")  # type: ignore[return-value]

    def profile(self, profile_version_id: str) -> dict[str, Any]:
        return {
            "id": profile_version_id, "code": "wan-i2v", "title": "Wan I2V", "version_no": 3,
            "capability": "VIDEO_I2V", "status": "PUBLISHED", "resources": {"vram_gb": 12},
        }

    def recent_terminal_attempts(self, profile_version_id: str, *, limit: int) -> dict[str, Any]:
        assert profile_version_id == "profile-version-1"
        assert limit == 100
        return {"schema_available": self.schema_available, "items": self.attempts, "query_count": 1}


def attempt(state: str, *, width: int = 1280, steps: int = 20) -> dict[str, Any]:
    return {
        "state": state, "channel": "GPU_H3", "resource_key": "GPU_H3_HEAVY",
        "input_snapshot_json": json.dumps({"semantic_inputs": {
            "width": width, "height": 720, "duration_seconds": 4, "frames": 97, "steps": steps,
        }}),
    }


def test_auto_recommendation_explains_profile_and_same_dimension_success_rate() -> None:
    repository = FakeRepository([
        attempt("SUCCEEDED"), attempt("FAILED"), attempt("SUCCEEDED"), attempt("SUCCEEDED"),
        attempt("FAILED", width=1920),
    ])
    result = GenerationPreferenceQueryService(repository).resolve(project_id="project-1", capability="VIDEO_I2V")  # type: ignore[arg-type]

    assert result["profile"] == {
        "code": "wan-i2v", "title": "Wan I2V", "version_no": 3,
        "capability": "VIDEO_I2V", "status": "PUBLISHED", "resources": {"vram_gb": 12},
    }
    assert result["recommendation"]["selection_reason"] == "AUTO_NEWEST_PUBLISHED_EXACT_CAPABILITY"
    rate = result["recommendation"]["local_success_rate"]
    assert rate["status"] == "AVAILABLE"
    assert rate["terminal_sample_count"] == 4
    assert rate["successful_sample_count"] == 3
    assert rate["value"] == 0.75
    assert rate["dimensions"]["gpu_class"] == "GPU_H3_HEAVY"
    assert rate["evidence"]["gpu_hardware_model_known"] is False


def test_auto_recommendation_keeps_rate_unknown_without_enough_comparable_attempts() -> None:
    repository = FakeRepository([attempt("SUCCEEDED"), attempt("FAILED")])
    result = GenerationPreferenceQueryService(repository).resolve(project_id="project-1", capability="VIDEO_I2V")  # type: ignore[arg-type]
    rate = result["recommendation"]["local_success_rate"]
    assert rate["status"] == "UNKNOWN"
    assert rate["reason"] == "INSUFFICIENT_SAME_DIMENSION_SAMPLES"
    assert rate["value"] is None
    assert rate["terminal_sample_count"] == 2


def test_terminal_attempt_read_is_bounded_and_excludes_non_authoritative_states() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE jobs (id TEXT PRIMARY KEY, execution_profile_version_id TEXT, input_snapshot_json TEXT, channel TEXT);
        CREATE TABLE job_attempts (id TEXT PRIMARY KEY, job_id TEXT, state TEXT, finished_at TEXT);
        CREATE TABLE job_resource_leases (id TEXT PRIMARY KEY, attempt_id TEXT, resource_key TEXT, acquired_at TEXT);
        INSERT INTO jobs VALUES ('j1','pv1','{}','GPU_H3'),('j2','pv1','{}','GPU_H3'),('j3','pv1','{}','GPU_H3');
        INSERT INTO job_attempts VALUES
          ('a1','j1','SUCCEEDED','2026-08-21T03:00:00+00:00'),
          ('a2','j2','FAILED','2026-08-21T02:00:00+00:00'),
          ('a3','j3','CANCELLED','2026-08-21T01:00:00+00:00');
        INSERT INTO job_resource_leases VALUES ('l1','a1','GPU_H3_HEAVY','2026-08-21T00:00:00+00:00');
        """
    )
    result = SqliteGenerationPreferenceRepository(connection).recent_terminal_attempts("pv1", limit=1)
    assert result["schema_available"] is True
    assert len(result["items"]) == 1
    assert result["items"][0]["state"] == "SUCCEEDED"
    assert result["items"][0]["resource_key"] == "GPU_H3_HEAVY"
    connection.close()

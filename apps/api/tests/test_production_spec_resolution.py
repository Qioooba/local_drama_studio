from __future__ import annotations

import sqlite3

from local_drama.application.production_spec_resolution import effective_video_profile, project_production_spec


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE projects (id TEXT PRIMARY KEY);
        CREATE TABLE project_profile_bindings (
            project_id TEXT, execution_profile_version_id TEXT, status TEXT
        );
        CREATE TABLE execution_profiles (id TEXT PRIMARY KEY, code TEXT, title TEXT);
        CREATE TABLE execution_profile_versions (
            id TEXT PRIMARY KEY, execution_profile_id TEXT, version_no INTEGER,
            capability TEXT, status TEXT, capability_json TEXT,
            model_bundle_json TEXT, parameter_schema_json TEXT,
            workflow_version_id TEXT, updated_at TEXT
        );
        CREATE TABLE generation_preference_sets (
            id TEXT PRIMARY KEY, project_id TEXT, owner_type TEXT, owner_id TEXT,
            capability TEXT, current_version_id TEXT, status TEXT,
            revision INTEGER, created_at TEXT, updated_at TEXT, created_by TEXT
        );
        CREATE TABLE generation_preference_versions (
            id TEXT PRIMARY KEY, preference_set_id TEXT,
            execution_profile_version_id TEXT, resolution_mode TEXT,
            settings_json TEXT, version_no INTEGER, reason TEXT,
            is_frozen INTEGER, created_at TEXT, created_by TEXT
        );
        """
    )
    connection.execute("INSERT INTO projects VALUES ('project-1')")
    connection.execute("INSERT INTO execution_profiles VALUES ('profile-auto', 'auto', 'Auto')")
    connection.execute(
        """INSERT INTO execution_profile_versions
        VALUES ('version-auto', 'profile-auto', 3, 'VIDEO_I2V', 'PUBLISHED', '{}', '{}', '{}', 'workflow-auto', '2026-09-01')"""
    )
    connection.execute(
        """INSERT INTO generation_preference_sets
        VALUES ('set-auto', 'project-1', 'PROJECT', 'project-1', 'VIDEO_I2V', 'pref-auto', 'ACTIVE', 1, 'now', 'now', 'test')"""
    )
    connection.execute(
        """INSERT INTO generation_preference_versions
        VALUES ('pref-auto', 'set-auto', NULL, 'AUTO', '{}', 1, 'test', 1, 'now', 'test')"""
    )
    return connection


def test_effective_video_profile_uses_project_auto_preference() -> None:
    connection = _connection()

    profile = effective_video_profile(connection, "project-1")

    assert profile is not None
    assert profile["id"] == "version-auto"
    assert profile["workflow_version_id"] == "workflow-auto"


def test_missing_production_plan_has_no_fixed_delivery_dimensions() -> None:
    # A missing plan is not permission to prescribe the last test project's
    # landscape canvas to a new portrait project.
    with sqlite3.connect(":memory:") as connection:
        result = project_production_spec(connection, "new-portrait-project", None)
    assert result["status"] == "BLOCKED"
    assert result["delivery"] is None
    assert result["generation"]["actual"] is None
    assert result["blockers"] == [{
        "code": "PRODUCTION_PLAN_NOT_BOUND",
        "message": "请在生产设置中确认并保存当前项目的画幅、分辨率和帧率",
    }]


def test_effective_video_profile_prefers_explicit_project_binding() -> None:
    connection = _connection()
    connection.execute("INSERT INTO execution_profiles VALUES ('profile-bound', 'bound', 'Bound')")
    connection.execute(
        """INSERT INTO execution_profile_versions
        VALUES ('version-bound', 'profile-bound', 1, 'VIDEO_I2V', 'PUBLISHED', '{}', '{}', '{}', 'workflow-bound', '2026-09-02')"""
    )
    connection.execute(
        "INSERT INTO project_profile_bindings VALUES ('project-1', 'version-bound', 'ACTIVE')"
    )

    profile = effective_video_profile(connection, "project-1")

    assert profile is not None
    assert profile["id"] == "version-bound"
    assert profile["workflow_version_id"] == "workflow-bound"

"""Populated upgrade coverage for canonical capabilities migration 0049."""

from __future__ import annotations

import uuid
from pathlib import Path

from tests.test_migration_0042 import _connect, _project, _upgrade_to

REVISION_0048 = "0048_asset_proposals"
HEAD = "0049_canonical_capabilities"


def test_upgrade_0048_to_0049_normalizes_legacy_capabilities(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-0048-0049.sqlite3"
    _upgrade_to(REVISION_0048, path)

    with _connect(path) as connection:
        project_id = _project(connection, "upgrade_0049")
        now = "2026-08-21T00:00:00Z"

        # Insert profiles and versions with legacy capabilities
        prof_llm_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO execution_profiles (id, code, title, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, 'LEGACY_LLM', 'Legacy LLM', ?, ?, 'test', 1, 'v1')""",
            (prof_llm_id, now, now),
        )
        pv_llm_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO execution_profile_versions (id, execution_profile_id, version_no, capability, status, model_bundle_json, input_contract_json, parameter_schema_json, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, 'SCRIPT_BREAKDOWN_LLM', 'PUBLISHED', '{}', '{}', '{}', ?, ?, 'test', 1, 'v1')""",
            (pv_llm_id, prof_llm_id, now, now),
        )

        prof_i2v_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO execution_profiles (id, code, title, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, 'LEGACY_I2V', 'Legacy I2V', ?, ?, 'test', 1, 'v1')""",
            (prof_i2v_id, now, now),
        )
        pv_i2v_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO execution_profile_versions (id, execution_profile_id, version_no, capability, status, model_bundle_json, input_contract_json, parameter_schema_json, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, 'I2V', 'PUBLISHED', '{}', '{}', '{}', ?, ?, 'test', 1, 'v1')""",
            (pv_i2v_id, prof_i2v_id, now, now),
        )

        # Insert legacy preference set
        set_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO generation_preference_sets (id, project_id, owner_type, owner_id, capability, current_version_id, status, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 'PROJECT', ?, 'I2V', NULL, 'ACTIVE', ?, ?, 'test', 1, 'v1')""",
            (set_id, project_id, project_id, now, now),
        )

    # Upgrade to 0049
    _upgrade_to(HEAD, path)

    with _connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD

        # Verify LLM capability was normalized
        row_llm_v = connection.execute("SELECT capability FROM execution_profile_versions WHERE id=?", (pv_llm_id,)).fetchone()
        assert row_llm_v["capability"] == "LLM_STORY_PARSE"

        # Verify I2V capability was normalized
        row_i2v_v = connection.execute("SELECT capability FROM execution_profile_versions WHERE id=?", (pv_i2v_id,)).fetchone()
        assert row_i2v_v["capability"] == "VIDEO_I2V"

        # Verify preference set was normalized
        row_set = connection.execute("SELECT capability FROM generation_preference_sets WHERE id=?", (set_id,)).fetchone()
        assert row_set["capability"] == "VIDEO_I2V"

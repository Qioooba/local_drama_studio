"""Safety coverage for immutable V2 ScopeOverrideSet migration."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from tests.test_migration_0042 import _connect, _upgrade_to


def test_upgrade_0083_refuses_to_silently_discard_out_of_band_assignment_json(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-0083.sqlite3"
    _upgrade_to("0082_model_platform_business_selection_rollouts", path)
    with _connect(path) as connection:
        capability_id = connection.execute(
            "SELECT id FROM mp_capability_definitions WHERE code='EMBEDDING_TEXT'"
        ).fetchone()[0]
        now = "2026-08-29T00:00:00Z"
        connection.execute(
            """INSERT INTO mp_capability_assignments
            (id,scope_type,scope_id,capability_definition_id,resolution_mode,execution_profile_version_id,override_json,revision,created_at,updated_at)
            VALUES (?,'SYSTEM','',?,'AUTO',NULL,'{"max_length":2048}',1,?,?)""",
            (str(uuid.uuid4()), capability_id, now, now),
        )

    with pytest.raises(RuntimeError, match="MP_SCOPE_OVERRIDE_MIGRATION_REQUIRED"):
        _upgrade_to("0083_model_platform_scope_override_set_versions", path)

    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "mp_scope_override_set_versions" not in tables

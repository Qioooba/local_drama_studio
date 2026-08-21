"""Targeted populated upgrade coverage for generation preferences 0043."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from tests.test_migration_0042 import _connect, _media, _project, _upgrade_to

REVISION_0041 = "0041_character_voice_bindings"
REVISION_0042 = "0042_asset_bible_states_references"
HEAD = "0043_generation_preferences"


def test_upgrade_0041_to_0042_to_0043_preserves_asset_and_preference_lineage(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-0041-0043.sqlite3"
    _upgrade_to(REVISION_0041, path)
    with _connect(path) as connection:
        project_id = _project(connection, "upgrade_0043")
        media_version_id = _media(connection, project_id)
        asset_id = str(uuid.uuid4())
        now = "2026-08-20T00:00:00Z"
        connection.execute(
            """INSERT INTO story_assets
            (id,project_id,kind,code,name,description,canonical_media_version_id,extra_json,status,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,'CHARACTER','HERO','Hero','',?,'{}','ACTIVE',?,?,?,1,'v2')""",
            (asset_id, project_id, media_version_id, now, now, "test"),
        )

    _upgrade_to(REVISION_0042, path)
    with _connect(path) as connection:
        hero = connection.execute(
            """SELECT media_version_id FROM story_asset_references
            WHERE story_asset_id=? AND reference_kind='HERO' AND status='ACTIVE'""",
            (asset_id,),
        ).fetchone()
        assert hero is not None and hero["media_version_id"] == media_version_id

    _upgrade_to(HEAD, path)
    with _connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD
        indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='index'")}
        assert {
            "ix_generation_preference_sets_resolution",
            "ix_generation_preference_versions_profile",
        } <= indexes

        preference_set_id = str(uuid.uuid4())
        preference_version_id = str(uuid.uuid4())
        now = "2026-08-20T00:00:00Z"
        connection.execute(
            """INSERT INTO generation_preference_sets
            (id,project_id,owner_type,owner_id,capability,current_version_id,status,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,'PROJECT',?,'VIDEO_I2V',NULL,'ACTIVE',?,?,?,1,'v1')""",
            (preference_set_id, project_id, project_id, now, now, "test"),
        )
        connection.execute(
            """INSERT INTO generation_preference_versions
            (id,preference_set_id,version_no,execution_profile_version_id,resolution_mode,settings_json,
             reason,is_frozen,created_at,created_by,schema_version)
            VALUES (?,?,1,NULL,'AUTO','{}','migration test',1,?,?,'v1')""",
            (preference_version_id, preference_set_id, now, "test"),
        )
        connection.execute(
            "UPDATE generation_preference_sets SET current_version_id=? WHERE id=?",
            (preference_version_id, preference_set_id),
        )
        current = connection.execute(
            """SELECT s.current_version_id,v.version_no,v.is_frozen
            FROM generation_preference_sets s
            JOIN generation_preference_versions v ON v.id=s.current_version_id
            WHERE s.id=?""",
            (preference_set_id,),
        ).fetchone()
        assert dict(current) == {
            "current_version_id": preference_version_id,
            "version_no": 1,
            "is_frozen": 1,
        }

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE generation_preference_sets SET current_version_id=? WHERE id=?",
                (str(uuid.uuid4()), preference_set_id),
            )

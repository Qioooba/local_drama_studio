"""Migration coverage for shot-scoped working media slots."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from tests.test_migration_0042 import _connect, _project, _season_episode_shot, _upgrade_to


def _legacy_keyframe_selection(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    shot_id: str,
    created_at: str,
) -> tuple[str, str, str]:
    asset_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    selection_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO media_assets
        (id,project_id,owner_type,owner_id,purpose,media_kind,created_at,updated_at,created_by,revision,schema_version)
        VALUES (?,?,'SHOT',?,'KEYFRAME','IMAGE',?,?,'test',1,'v2')""",
        (asset_id, project_id, shot_id, created_at, created_at),
    )
    connection.execute(
        """INSERT INTO media_versions
        (id,media_asset_id,version_no,take_no,stage,rel_path,mime_type,byte_size,sha256,integrity_status,
         created_at,updated_at,created_by,revision,schema_version)
        VALUES (?,?,1,1,'KEYFRAME',?,'image/png',1,?,'VERIFIED',?,?,'test',1,'v2')""",
        (version_id, asset_id, f"media/{version_id}.png", version_id.replace("-", "")[:64], created_at, created_at),
    )
    connection.execute(
        """INSERT INTO selections
        (id,media_asset_id,media_version_id,selection_type,source_revision,created_at,updated_at,created_by,revision,schema_version)
        VALUES (?,?,?,'KEYFRAME',1,?,?,'test',1,'v2')""",
        (selection_id, asset_id, version_id, created_at, created_at),
    )
    return asset_id, version_id, selection_id


def test_0061_collapses_legacy_per_asset_choices_to_latest_shot_slot(tmp_path: Path) -> None:
    path = tmp_path / "legacy-selections.sqlite3"
    _upgrade_to("0060_visual_lab_runtime_foundation", path)
    with _connect(path) as connection:
        project_id = _project(connection, "working-slot-backfill")
        _season_id, _episode_id, shot_id = _season_episode_shot(connection, project_id)
        _old_asset, old_version, _old_selection = _legacy_keyframe_selection(
            connection,
            project_id=project_id,
            shot_id=shot_id,
            created_at="2026-08-20T00:00:00Z",
        )
        _new_asset, new_version, new_selection = _legacy_keyframe_selection(
            connection,
            project_id=project_id,
            shot_id=shot_id,
            created_at="2026-08-21T00:00:00Z",
        )

    _upgrade_to("0061_shot_working_media_slots", path)

    with _connect(path) as connection:
        rows = connection.execute(
            "SELECT * FROM shot_working_media_slots WHERE shot_id=?",
            (shot_id,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["slot_type"] == "KEYFRAME"
        assert rows[0]["media_version_id"] == new_version
        assert rows[0]["adopted_from_selection_id"] == new_selection
        assert rows[0]["media_version_id"] != old_version
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO shot_working_media_slots
                (id,shot_id,slot_type,media_version_id,adopted_at,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?, 'KEYFRAME',?,'2026-08-22','2026-08-22','2026-08-22','test',1,'v1')""",
                (str(uuid.uuid4()), shot_id, old_version),
            )

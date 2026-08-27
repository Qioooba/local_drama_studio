"""Migration coverage for canonical Post / Audio mix revisions."""

from __future__ import annotations

import uuid
from pathlib import Path

from tests.test_migration_0042 import _connect, _project, _season_episode_shot, _upgrade_to


def test_0063_backfills_audio_mix_revision_from_existing_bindings(tmp_path: Path) -> None:
    path = tmp_path / "legacy-audio.sqlite3"
    _upgrade_to("0062_canonical_job_scope_stage", path)
    with _connect(path) as connection:
        project_id = _project(connection, "audio-mix-backfill")
        _season_id, episode_id, _shot_id = _season_episode_shot(connection, project_id)
        asset_id, media_id, binding_id = (str(uuid.uuid4()) for _ in range(3))
        now = "2026-08-26T00:00:00Z"
        connection.execute(
            """INSERT INTO media_assets
            (id,project_id,owner_type,owner_id,purpose,media_kind,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,'EPISODE',?,'POST_AUDIO_SOURCE','AUDIO',?,?,'test',1,'v2')""",
            (asset_id, project_id, episode_id, now, now),
        )
        connection.execute(
            """INSERT INTO media_versions
            (id,media_asset_id,version_no,take_no,stage,rel_path,mime_type,byte_size,sha256,integrity_status,duration_ms,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,1,1,'FORMAL','media/audio.wav','audio/wav',1,?,'VERIFIED',1000,?,?,'test',1,'v2')""",
            (media_id, asset_id, "a" * 64, now, now),
        )
        connection.execute(
            """INSERT INTO audio_bindings
            (id,episode_id,media_version_id,track_type,start_us,end_us,gain_db,source_license_status,status,snapshot_json,
             loop_enabled,fade_in_us,fade_out_us,license_evidence_json,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,'BGM',0,500000,0,'USER_OWNED','ACTIVE','{}',0,0,0,'{}',?,?,'test',1,'v2')""",
            (binding_id, episode_id, media_id, now, now),
        )

    _upgrade_to("0063_audio_mix_drafts", path)

    with _connect(path) as connection:
        row = connection.execute(
            "SELECT episode_id,revision,status FROM audio_mix_drafts WHERE episode_id=?", (episode_id,)
        ).fetchone()
        assert dict(row) == {"episode_id": episode_id, "revision": 1, "status": "DRAFT"}
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

"""Migration coverage for canonical Job identity, scope and production stage."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from tests.test_migration_0042 import _connect, _project, _season_episode_shot, _upgrade_to


def test_0062_backfills_job_scope_and_enforces_stage_registry(tmp_path: Path) -> None:
    path = tmp_path / "legacy-jobs.sqlite3"
    _upgrade_to("0061_shot_working_media_slots", path)
    with _connect(path) as connection:
        project_id = _project(connection, "canonical-job-backfill")
        _season_id, episode_id, shot_id = _season_episode_shot(connection, project_id)
        job_id = str(uuid.uuid4())
        now = "2026-08-26T00:00:00Z"
        connection.execute(
            """INSERT INTO jobs
            (id,type,project_id,subject_type,subject_id,state,channel,idempotency_key,input_snapshot_json,
             execution_profile_version_id,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,'VIDEO_GENERATION',?,'SHOT',?,'QUEUED','GPU_H3','legacy-job','{}',NULL,?,?, 'test',1,'v2')""",
            (job_id, project_id, shot_id, now, now),
        )
        attempt_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO job_attempts
            (id,job_id,attempt_no,state,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,1,'RUNNING',?,?, 'test',1,'v2')""",
            (attempt_id, job_id, now, now),
        )

    _upgrade_to("0062_canonical_job_scope_stage", path)

    with _connect(path) as connection:
        row = connection.execute(
            """SELECT subject_kind,scope_project_id,scope_episode_id,scope_shot_id,stage_code
            FROM jobs WHERE id=?""",
            (job_id,),
        ).fetchone()
        assert dict(row) == {
            "subject_kind": "SHOT",
            "scope_project_id": project_id,
            "scope_episode_id": episode_id,
            "scope_shot_id": shot_id,
            "stage_code": "VIDEO",
        }
        assert connection.execute("SELECT COUNT(*) FROM job_stage_definitions WHERE active=1").fetchone()[0] >= 8
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            connection.execute(
                "SELECT job_id FROM job_attempts WHERE id=?",
                (attempt_id,),
            ).fetchone()[0]
            == job_id
        )
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE jobs SET stage_code='RUNTIME_GUESSED_STAGE' WHERE id=?",
                (job_id,),
            )

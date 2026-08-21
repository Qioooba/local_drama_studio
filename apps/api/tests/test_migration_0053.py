"""Populated forward-repair coverage for canonical capability migration 0053."""

from __future__ import annotations

import runpy
import uuid
from pathlib import Path

import sqlalchemy as sa

from tests.test_migration_0042 import _connect, _project, _upgrade_to

PREVIOUS = "0052_storage_operations"
HEAD = "0053_canonical_capability_repair"


def _profile_version(connection, *, capability: str) -> str:
    profile_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    now = "2026-08-21T00:00:00Z"
    connection.execute(
        """INSERT INTO execution_profiles
        (id,code,title,created_at,updated_at,created_by,revision,schema_version)
        VALUES (?,?,?,?,?,'test',1,'v2')""",
        (profile_id, f"profile-{profile_id}", capability, now, now),
    )
    connection.execute(
        """INSERT INTO execution_profile_versions
        (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,
         parameter_schema_json,status,created_at,updated_at,created_by,revision,schema_version)
        VALUES (?,?,1,?,'{}','{}','{}','PUBLISHED',?,?,'test',1,'v2')""",
        (version_id, profile_id, capability, now, now),
    )
    return version_id


def _binding(
    connection,
    *,
    project_id: str,
    profile_version_id: str,
    capability: str,
    status: str = "ACTIVE",
    created_at: str = "2026-08-21T00:00:00Z",
) -> str:
    binding_id = str(uuid.uuid4())
    connection.execute(
        """INSERT INTO project_profile_bindings
        (id,project_id,capability,execution_profile_version_id,status,created_at,updated_at,
         created_by,revision,schema_version)
        VALUES (?,?,?,?,?,?,?,'test',1,'v2')""",
        (binding_id, project_id, capability, profile_version_id, status, created_at, created_at),
    )
    return binding_id


def _repair_helper():
    root = Path(__file__).resolve().parents[3]
    namespace = runpy.run_path(
        str(root / "apps" / "api" / "alembic" / "versions" / "0053_canonical_capability_repair.py")
    )
    return namespace["_repair_project_profile_bindings"]


def test_populated_upgrade_quarantines_without_guessing_and_reports_counts(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-0052-0053.sqlite3"
    _upgrade_to(PREVIOUS, path)
    with _connect(path) as connection:
        profile_version_id = _profile_version(connection, capability="I2V")
        t2v_profile_version_id = _profile_version(connection, capability="T2V")
        duplicate_project = _project(connection, "capability_duplicate")
        blank_project = _project(connection, "capability_blank")
        ambiguous_project = _project(connection, "capability_ambiguous")
        unknown_project = _project(connection, "capability_unknown")
        lower_project = _project(connection, "capability_lower")

        active_alias_id = _binding(
            connection,
            project_id=duplicate_project,
            profile_version_id=profile_version_id,
            capability="I2V",
            status="ACTIVE",
            created_at="2026-08-21T00:00:00Z",
        )
        duplicate_canonical_id = _binding(
            connection,
            project_id=duplicate_project,
            profile_version_id=profile_version_id,
            capability="VIDEO_I2V",
            status="SELECTED_CANDIDATE",
            created_at="2026-08-21T00:00:01Z",
        )
        blank_id = _binding(
            connection,
            project_id=blank_project,
            profile_version_id=profile_version_id,
            capability="   ",
        )
        ambiguous_id = _binding(
            connection,
            project_id=ambiguous_project,
            profile_version_id=profile_version_id,
            capability="IMAGE_GENERATION",
        )
        unknown_id = _binding(
            connection,
            project_id=unknown_project,
            profile_version_id=profile_version_id,
            capability="UNKNOWN_MODEL_X",
        )
        lower_alias_id = _binding(
            connection,
            project_id=lower_project,
            profile_version_id=t2v_profile_version_id,
            capability=" t2v ",
        )

    _upgrade_to(HEAD, path)

    with _connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD
        active_alias = connection.execute(
            "SELECT capability,status FROM project_profile_bindings WHERE id=?", (active_alias_id,)
        ).fetchone()
        assert dict(active_alias) == {"capability": "VIDEO_I2V", "status": "ACTIVE"}
        assert connection.execute(
            "SELECT capability FROM project_profile_bindings WHERE id=?", (lower_alias_id,)
        ).fetchone()[0] == "VIDEO_T2V"

        for row_id in (duplicate_canonical_id, blank_id, ambiguous_id, unknown_id):
            row = connection.execute(
                "SELECT capability,status FROM project_profile_bindings WHERE id=?", (row_id,)
            ).fetchone()
            assert row["status"] == "QUARANTINED"
            assert row["capability"] == f"QUARANTINED:{row_id}"

        reasons = {
            row["source_row_id"]: row["reason_code"]
            for row in connection.execute(
                "SELECT source_row_id,reason_code FROM capability_migration_quarantine"
            ).fetchall()
        }
        assert reasons == {
            duplicate_canonical_id: "CAPABILITY_DUPLICATE_CANONICAL_KEY",
            blank_id: "CAPABILITY_NULL_OR_BLANK",
            ambiguous_id: "CAPABILITY_AMBIGUOUS",
            unknown_id: "CAPABILITY_UNKNOWN",
        }
        report = connection.execute(
            "SELECT * FROM capability_migration_reports WHERE migration_revision=?", (HEAD,)
        ).fetchone()
        assert report["scanned_count"] == 6
        assert report["normalized_count"] == 2
        assert report["execution_profile_normalized_count"] == 2
        assert report["quarantined_null_count"] == 1
        assert report["quarantined_ambiguous_count"] == 1
        assert report["quarantined_unknown_count"] == 1
        assert report["quarantined_duplicate_count"] == 1
        assert connection.execute(
            "SELECT capability FROM execution_profile_versions WHERE id=?", (profile_version_id,)
        ).fetchone()[0] == "VIDEO_I2V"


def test_repair_is_idempotent_and_operator_correction_is_recoverable(tmp_path: Path) -> None:
    path = tmp_path / "repair-recovery-0053.sqlite3"
    _upgrade_to(PREVIOUS, path)
    with _connect(path) as connection:
        profile_version_id = _profile_version(connection, capability="SCENE")
        project_id = _project(connection, "capability_recovery")
        binding_id = _binding(
            connection,
            project_id=project_id,
            profile_version_id=profile_version_id,
            capability="UNKNOWN_SCENE_PROVIDER",
        )

    _upgrade_to(HEAD, path)
    with _connect(path) as connection:
        connection.execute(
            "UPDATE project_profile_bindings SET capability='scene',status='ACTIVE' WHERE id=?",
            (binding_id,),
        )

    repair = _repair_helper()
    engine = sa.create_engine(f"sqlite:///{path.as_posix()}")
    with engine.begin() as connection:
        recovered = repair(connection, now="2026-08-21T01:00:00Z")
        assert recovered["normalized_count"] == 1
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT capability,status,revision FROM project_profile_bindings WHERE id=?", (binding_id,)
        ).fetchone()
        assert row["capability"] == "IMAGE_SCENE"
        assert row["status"] == "ACTIVE"
        revision_after_recovery = int(row["revision"])
        quarantine = connection.execute(
            "SELECT resolved_at,resolution_action FROM capability_migration_quarantine WHERE source_row_id=?",
            (binding_id,),
        ).fetchone()
        assert quarantine["resolved_at"] == "2026-08-21T01:00:00Z"
        assert quarantine["resolution_action"] == "OPERATOR_CORRECTED_AND_REACTIVATED"

    with engine.begin() as connection:
        repeated = repair(connection, now="2026-08-21T02:00:00Z")
        assert repeated["normalized_count"] == 0
        assert repeated["quarantined_unknown_count"] == 0
        assert repeated["quarantined_duplicate_count"] == 0
    engine.dispose()
    with _connect(path) as connection:
        assert connection.execute(
            "SELECT revision FROM project_profile_bindings WHERE id=?", (binding_id,)
        ).fetchone()[0] == revision_after_recovery
        assert connection.execute("SELECT COUNT(*) FROM capability_migration_quarantine").fetchone()[0] == 1


def test_classifier_distinguishes_null_ambiguous_and_unknown() -> None:
    root = Path(__file__).resolve().parents[3]
    namespace = runpy.run_path(
        str(root / "apps" / "api" / "alembic" / "versions" / "0053_canonical_capability_repair.py")
    )
    classify = namespace["_classify_capability"]
    assert classify(None) == ("NULL_OR_BLANK", None)
    assert classify("IMAGE_GENERATION") == ("AMBIGUOUS", None)
    assert classify("provider_magic") == ("UNKNOWN", None)
    assert classify(" i2v ") == ("ALIAS", "VIDEO_I2V")

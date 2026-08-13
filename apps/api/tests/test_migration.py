from __future__ import annotations

import sqlite3

from local_drama.infrastructure.database.backup import online_backup
from local_drama.infrastructure.database.sqlite import Database


def test_g2_migration_is_real_wal_schema(database: Database) -> None:
    assert database.exists
    assert database.wal_mode() == "wal"
    assert database.integrity_check() == "ok"
    with database.connect() as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        variant_columns = {row[1] for row in connection.execute("PRAGMA table_info(generation_variants)")}
        anchor_columns = {row[1] for row in connection.execute("PRAGMA table_info(frame_anchors)")}
        media_columns = {row[1] for row in connection.execute("PRAGMA table_info(media_versions)")}
        profile_columns = {row[1] for row in connection.execute("PRAGMA table_info(execution_profile_versions)")}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        assert version == "0020_g7_model_license_evidence"
    assert "provider_random_nonce" in variant_columns
    assert {"requested_time_us", "resolved_time_us", "source_sha256", "extraction_method"} <= anchor_columns
    assert "source_artifact_id" in media_columns
    assert {"output_contract_json", "resource_policy_json"} <= profile_columns
    assert foreign_keys == 1
    expected = {
        "projects",
        "shots",
        "media_versions",
        "review_decisions",
        "jobs",
        "generation_variants",
        "outbox_events",
        "local_runtimes",
        "source_documents",
        "diagnostic_runs",
        "fts_search",
        "review_annotations",
        "review_batch_plans",
        "subtitle_revisions",
        "subtitle_cues",
        "audio_bindings",
        "post_process_recipes",
        "enhancement_runs",
        "delivery_events",
        "canvas_layouts",
        "workflow_validation_attestations",
        "profile_validation_attestations",
        "profile_compatibility_attestations",
        "g7_network_e2e_attestations",
        "workspace_asset_authorizations",
        "brand_kits",
        "model_compatibility_reports",
        "model_license_evidence",
        "canvas_execution_plans",
        "prompts",
        "prompt_revisions",
    }
    assert expected <= tables


def test_backup_is_online_and_verifiable(database: Database, workspace) -> None:
    destination = workspace.backups_root / "g2_test.sqlite3"
    assert database.integrity_check() == "ok"
    assert online_backup(database.path, destination) == "ok"
    with sqlite3.connect(destination) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

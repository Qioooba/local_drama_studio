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
        shot_columns = {row[1] for row in connection.execute("PRAGMA table_info(shots)")}
        anchor_columns = {row[1] for row in connection.execute("PRAGMA table_info(frame_anchors)")}
        media_columns = {row[1] for row in connection.execute("PRAGMA table_info(media_versions)")}
        profile_columns = {row[1] for row in connection.execute("PRAGMA table_info(execution_profile_versions)")}
        render_columns = {row[1] for row in connection.execute("PRAGMA table_info(episode_render_versions)")}
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        attempt_columns = {row[1] for row in connection.execute("PRAGMA table_info(job_attempts)")}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert version == "0054_character_identity_pack_hardening"
    assert {"provider_random_nonce", "director_recipe_version_id", "director_recipe_hash"} <= variant_columns
    assert {"scene_id", "source_shot_id", "archived_at"} <= shot_columns
    assert {"requested_time_us", "resolved_time_us", "source_sha256", "extraction_method"} <= anchor_columns
    assert "source_artifact_id" in media_columns
    assert {"output_contract_json", "resource_policy_json"} <= profile_columns
    assert {"input_snapshot_json", "ffmpeg_command_json", "execution_log_text"} <= render_columns
    assert {"progress_json", "progress_updated_at", "started_at", "finished_at", "last_error_detail_redacted"} <= job_columns
    assert {"progress_json", "started_at", "finished_at"} <= attempt_columns
    with database.connect() as connection:
        project_columns = {row[1] for row in connection.execute("PRAGMA table_info(projects)")}
    assert {"width", "height", "primary_language", "subtitle_mode", "subtitle_language"} <= project_columns
    assert foreign_keys == 1
    assert {
        "ix_media_assets_owner",
        "ix_media_versions_asset_version",
        "ix_generation_intents_owner",
        "ix_jobs_subject_state",
        "ix_selections_asset_type",
        "ix_review_decisions_subject_created",
        "ix_timeline_revisions_episode_revision",
        "ix_subtitle_revisions_episode_revision",
        "ix_episode_renders_episode_created",
        "ix_delivery_packages_render_created",
        "ix_project_package_imports_status_updated",
        "ix_video_review_annotations_media_time",
        "ix_episode_scene_ranges_episode_order",
        "ix_episode_scene_ranges_scene",
        "ix_creative_entries_project_kind",
        "ix_creative_entry_revisions_entry",
        "ix_project_asset_grants_target_status",
        "ix_project_asset_grants_source_media",
        "ix_outbox_delivery_attempts_endpoint_status_next",
        "ix_outbox_delivery_attempts_event_endpoint",
        "ix_automation_workflow_run_tasks_job",
        "ix_story_assets_project_kind",
        "ix_shot_asset_bindings_asset",
        "ix_shot_asset_bindings_shot",
        "ix_character_voice_bindings_project",
        "ix_director_recipe_versions_recipe",
        "ix_generation_variants_director_recipe",
        "ix_shots_scene_order",
        "ix_shot_groups_episode_order",
        "ix_generation_qc_policy_resolution",
        "ix_variant_qc_links_variant_created",
        "ix_shots_source_shot",
        "ix_shots_episode_archived_order",
        "ix_asset_proposals_project_status",
        "ix_worker_sessions_status_lease",
        "ix_job_attempts_worker_session",
        "ix_storage_operations_status_updated",
    } <= indexes
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
        "project_package_imports",
        "video_review_annotations",
        "episode_scene_ranges",
        "creative_entries",
        "creative_entry_revisions",
        "canvas_execution_plans",
        "prompts",
        "prompt_revisions",
        "project_asset_grants",
        "automation_clients",
        "webhook_subscriptions",
        "webhook_deliveries",
        "automation_workflows",
        "automation_workflow_runs",
        "automation_workflow_run_tasks",
        "automation_workflow_run_events",
        "motion_controls",
        "job_resource_leases",
        "outbox_delivery_attempts",
        "story_assets",
        "shot_asset_bindings",
        "character_voice_bindings",
        "director_recipes",
        "director_recipe_versions",
        "project_director_recipe_bindings",
        "shot_groups",
        "shot_group_members",
        "generation_qc_policy_sets",
        "generation_qc_policy_versions",
        "variant_qc_links",
        "story_asset_proposals",
        "worker_sessions",
        "storage_operations",
    }
    assert expected <= tables


def test_backup_is_online_and_verifiable(database: Database, workspace) -> None:
    destination = workspace.backups_root / "g2_test.sqlite3"
    assert database.integrity_check() == "ok"
    assert online_backup(database.path, destination) == "ok"
    with sqlite3.connect(destination) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

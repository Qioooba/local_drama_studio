from __future__ import annotations

import sqlite3

from local_drama.infrastructure.database.backup import online_backup
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.domain.capabilities import CAPABILITY_DEFINITIONS


def test_g2_migration_is_real_wal_schema(database: Database) -> None:
    assert database.exists
    assert database.wal_mode() == "wal"
    assert database.integrity_check() == "ok"
    with database.connect() as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        variant_columns = {row[1] for row in connection.execute("PRAGMA table_info(generation_variants)")}
        shot_columns = {row[1] for row in connection.execute("PRAGMA table_info(shots)")}
        visual_lab_document_columns = {row[1] for row in connection.execute("PRAGMA table_info(visual_lab_documents)")}
        anchor_columns = {row[1] for row in connection.execute("PRAGMA table_info(frame_anchors)")}
        media_columns = {row[1] for row in connection.execute("PRAGMA table_info(media_versions)")}
        profile_columns = {row[1] for row in connection.execute("PRAGMA table_info(execution_profile_versions)")}
        render_columns = {row[1] for row in connection.execute("PRAGMA table_info(episode_render_versions)")}
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        attempt_columns = {row[1] for row in connection.execute("PRAGMA table_info(job_attempts)")}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        working_slot_columns = {row[1] for row in connection.execute("PRAGMA table_info(shot_working_media_slots)")}
        quick_run_columns = {row[1] for row in connection.execute("PRAGMA table_info(quick_generation_runs)")}
        execution_snapshot_columns = {row[1] for row in connection.execute("PRAGMA table_info(mp_execution_snapshots)")}
        assert version == "0086_model_platform_quick_create_v2_runs"
        seeded_capability_count = connection.execute("SELECT COUNT(*) FROM mp_capability_definitions").fetchone()[0]
        embedding_definition = connection.execute(
            "SELECT family, background_only FROM mp_capability_definitions WHERE code = 'EMBEDDING_TEXT'"
        ).fetchone()
    assert {"provider_random_nonce", "director_recipe_version_id", "director_recipe_hash"} <= variant_columns
    assert seeded_capability_count == len(CAPABILITY_DEFINITIONS)
    assert tuple(embedding_definition) == ("RETRIEVAL", 1)
    assert {"quick_generation_runs", "quick_generation_candidates", "quick_generation_outputs", "quick_generation_events", "quick_generation_presets"} <= tables
    assert "model_parameters_json" in quick_run_columns
    assert {"runtime_configuration_json", "model_bindings_json", "execution_binding_json"} <= execution_snapshot_columns
    assert "mp_runtime_model_workflow_bindings" in tables
    assert "mp_legacy_profile_version_crosswalks" in tables
    assert "mp_business_selection_rollouts" in tables
    assert "mp_scope_override_set_versions" in tables
    assert {"mp_project_knowledge_index_runs", "mp_project_knowledge_index_batches", "mp_project_knowledge_vectors"} <= tables
    assert {"mp_quick_create_v2_runs", "mp_quick_create_v2_steps"} <= tables
    knowledge_index_columns = {row[1] for row in connection.execute("PRAGMA table_info(mp_project_knowledge_index_runs)")}
    assert {"attempt_no", "retry_of_index_run_id"} <= knowledge_index_columns
    quick_v2_run_columns = {row[1] for row in connection.execute("PRAGMA table_info(mp_quick_create_v2_runs)")}
    quick_v2_step_columns = {row[1] for row in connection.execute("PRAGMA table_info(mp_quick_create_v2_steps)")}
    assert {"idempotency_key", "selected_step_id", "final_step_id", "input_hash", "plan_json"} <= quick_v2_run_columns
    assert {"execution_snapshot_id", "job_id", "input_artifact_id", "output_artifact_id", "content_hash"} <= quick_v2_step_columns
    assignment_columns = {row[1] for row in connection.execute("PRAGMA table_info(mp_capability_assignments)")}
    assert "override_set_version_id" in assignment_columns
    with database.connect() as connection:
        video_profile_column = next(row for row in connection.execute("PRAGMA table_info(quick_generation_runs)") if row[1] == "video_profile_version_id")
    assert video_profile_column[3] == 0
    assert not {"one_sentence_video_runs", "one_sentence_video_candidates", "one_sentence_video_run_events"} & tables
    assert {"scene_id", "source_shot_id", "archived_at"} <= shot_columns
    assert "viewport_json" in visual_lab_document_columns
    assert {"shot_id", "slot_type", "media_version_id", "adopted_from_selection_id", "revision"} <= working_slot_columns
    assert {"requested_time_us", "resolved_time_us", "source_sha256", "extraction_method"} <= anchor_columns
    assert "source_artifact_id" in media_columns
    assert {"output_contract_json", "resource_policy_json"} <= profile_columns
    assert {"input_snapshot_json", "ffmpeg_command_json", "execution_log_text"} <= render_columns
    assert {"progress_json", "progress_updated_at", "started_at", "finished_at", "last_error_detail_redacted"} <= job_columns
    assert {"subject_kind", "scope_kind", "scope_project_id", "scope_episode_id", "scope_shot_id", "stage_code"} <= job_columns
    assert "job_stage_definitions" in tables
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
        "ix_provider_connections_kind_status",
        "ix_automation_workflows_project_code_version",
        "ix_provider_execution_events_attempt_time",
        "ix_visual_lab_documents_project_updated",
        "ix_visual_lab_nodes_document_z",
        "ix_visual_lab_node_revisions_node_no",
        "ix_visual_lab_edges_document",
        "ix_visual_lab_snapshots_document_no",
        "ix_runtime_environment_versions_environment_no",
        "ix_runtime_instances_environment_created",
        "ix_workflow_contract_versions_workflow_no",
        "ix_shot_working_media_slots_version",
        "uq_gpu_runtime_leases_active_resource",
        "ix_gpu_runtime_leases_owner_ref",
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
        "provider_connections",
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
        "visual_lab_documents",
        "visual_lab_nodes",
        "visual_lab_node_revisions",
        "visual_lab_edges",
        "visual_lab_snapshots",
        "visual_lab_promotions",
        "runtime_environments",
        "runtime_environment_versions",
        "workflow_app_contract_versions",
        "workflow_runtime_bindings",
        "runtime_instances",
        "provider_execution_events",
        "shot_working_media_slots",
        "gpu_runtime_leases",
        "gpu_runtime_state",
        "embedding_indexes",
        "embedding_chunks",
        "speech_alignment_runs",
        "mp_compute_nodes",
        "mp_model_libraries",
        "mp_model_families",
        "mp_model_releases",
        "mp_model_artifacts",
        "mp_model_artifact_locations",
        "mp_model_components",
        "mp_runtime_installations",
        "mp_runtime_installation_versions",
        "mp_runtime_instances",
        "mp_runtime_model_installations",
        "mp_capability_definitions",
        "mp_capability_offerings",
        "mp_parameter_contract_versions",
        "mp_adapter_binding_contract_versions",
        "mp_resource_policy_versions",
        "mp_execution_profiles",
        "mp_execution_profile_versions",
        "mp_profile_publications",
        "mp_capability_assignments",
        "mp_discovery_runs",
        "mp_discovery_observations",
        "mp_validation_runs",
        "mp_validation_evidence",
        "mp_install_plans",
        "mp_install_jobs",
        "mp_execution_snapshots",
        "mp_execution_job_links",
        "mp_runtime_model_workflow_bindings",
        "mp_comfy_capability_smoke_jobs",
    }
    assert expected <= tables


def test_backup_is_online_and_verifiable(database: Database, workspace) -> None:
    destination = workspace.backups_root / "g2_test.sqlite3"
    assert database.integrity_check() == "ok"
    assert online_backup(database.path, destination) == "ok"
    with sqlite3.connect(destination) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

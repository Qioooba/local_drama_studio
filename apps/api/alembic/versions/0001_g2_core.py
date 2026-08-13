"""G2 core domain schema.

Revision ID: 0001_g2_core
Revises:
"""

import sqlalchemy as sa

from alembic import op

revision = "0001_g2_core"
down_revision = None
branch_labels = None
depends_on = None


def _audit_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by", sa.Text(), nullable=False, server_default=sa.text("'system'")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default=sa.text("'v2'")),
    )


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("template_version", sa.String(100), nullable=False),
        sa.Column("root_rel", sa.Text(), nullable=False),
        sa.Column("production_plan_version_id", sa.String(36)),
        sa.Column("aspect_ratio", sa.String(20)),
        sa.Column("fps_num", sa.Integer()),
        sa.Column("fps_den", sa.Integer()),
        sa.Column("timezone", sa.String(64), nullable=False, server_default=sa.text("'Asia/Shanghai'")),
        sa.Column("archived_at", sa.Text()),
        *_audit_columns(),
        sa.UniqueConstraint("code", name="uq_projects_code"),
        sa.CheckConstraint("status IN ('DRAFT','ACTIVE','PAUSED','ARCHIVED')", name="ck_projects_status"),
        sa.CheckConstraint("fps_num IS NULL OR fps_num > 0", name="ck_projects_fps_num"),
        sa.CheckConstraint("fps_den IS NULL OR fps_den > 0", name="ck_projects_fps_den"),
    )
    op.create_table(
        "seasons",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200)),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "number", name="uq_seasons_project_number"),
        sa.UniqueConstraint("project_id", "code", name="uq_seasons_project_code"),
    )
    op.create_table(
        "episodes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("season_id", sa.String(36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200)),
        sa.Column("narrative_status", sa.String(24), nullable=False),
        sa.Column("production_status", sa.String(24), nullable=False),
        sa.Column("target_duration_ms", sa.Integer(), nullable=False),
        sa.Column("source_range_json", sa.Text(), nullable=False, server_default="{}"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("season_id", "number", name="uq_episodes_season_number"),
        sa.UniqueConstraint("season_id", "code", name="uq_episodes_season_code"),
        sa.CheckConstraint("target_duration_ms > 0", name="ck_episodes_duration"),
    )
    op.create_table(
        "scenes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200)),
        sa.Column("location", sa.Text()),
        sa.Column("time_of_day", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "code", name="uq_scenes_project_code"),
    )
    op.create_table(
        "shots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("episode_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("order_key", sa.String(64), nullable=False),
        sa.Column("target_duration_ms", sa.Integer(), nullable=False),
        sa.Column("shot_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("current_revision_id", sa.String(36)),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("episode_id", "code", name="uq_shots_episode_code"),
        sa.CheckConstraint("target_duration_ms > 0", name="ck_shots_duration"),
    )
    op.create_table(
        "shot_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("shot_id", sa.String(36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("fields_json", sa.Text(), nullable=False),
        sa.Column("is_frozen", sa.Integer(), nullable=False, server_default="0"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["shot_id"], ["shots.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("shot_id", "revision_no", name="uq_shot_revisions_no"),
    )

    op.create_table(
        "media_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("owner_type", sa.String(40), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("media_kind", sa.String(24), nullable=False),
        sa.Column("selected_version_id", sa.String(36)),
        sa.Column("approved_version_id", sa.String(36)),
        sa.Column("version_counter", sa.Integer(), nullable=False, server_default="0"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "media_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("media_asset_id", sa.String(36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("take_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("rel_path", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("fps_num", sa.Integer()),
        sa.Column("fps_den", sa.Integer()),
        sa.Column("parent_version_id", sa.String(36)),
        sa.Column("source_job_attempt_id", sa.String(36)),
        sa.Column("integrity_status", sa.String(24), nullable=False, server_default=sa.text("'UNKNOWN'")),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["media_asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("media_asset_id", "version_no", name="uq_media_versions_no"),
        sa.CheckConstraint("byte_size >= 0", name="ck_media_versions_size"),
    )
    op.create_table(
        "selections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("media_asset_id", sa.String(36), nullable=False),
        sa.Column("media_version_id", sa.String(36), nullable=False),
        sa.Column("selection_type", sa.String(32), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["media_asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_version_id"], ["media_versions.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "review_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("subject_type", sa.String(40), nullable=False),
        sa.Column("items_json", sa.Text(), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("code", "version_no", name="uq_review_templates_version"),
    )
    op.create_table(
        "review_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("subject_type", sa.String(40), nullable=False),
        sa.Column("subject_id", sa.String(36), nullable=False),
        sa.Column("review_template_version_id", sa.String(36), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("supersedes_decision_id", sa.String(36)),
        *_audit_columns(),
    )
    op.create_table(
        "review_checks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("review_decision_id", sa.String(36), nullable=False),
        sa.Column("item_id", sa.String(80), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.ForeignKeyConstraint(["review_decision_id"], ["review_decisions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("review_decision_id", "item_id", name="uq_review_checks_item"),
    )
    op.create_table(
        "machine_check_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("subject_type", sa.String(40), nullable=False),
        sa.Column("subject_id", sa.String(36), nullable=False),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
    )
    op.create_table(
        "machine_check_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("item_id", sa.String(80), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["machine_check_runs.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "providers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        *_audit_columns(),
    )
    op.create_table(
        "provider_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider_id", sa.String(36), nullable=False),
        sa.Column("transport", sa.String(32), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "models",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(120), nullable=False, unique=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("machine_path_ref", sa.Text()),
        sa.Column("license_note", sa.Text()),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
    )
    op.create_table(
        "model_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_id", sa.String(36), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("compatibility_json", sa.Text(), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "workflows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(120), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        *_audit_columns(),
    )
    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workflow_id", sa.String(36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("contract_json", sa.Text(), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workflow_id", "version_no", name="uq_workflow_versions_no"),
    )
    op.create_table(
        "execution_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(120), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        *_audit_columns(),
    )
    op.create_table(
        "execution_profile_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("execution_profile_id", sa.String(36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("capability", sa.String(120), nullable=False),
        sa.Column("runtime_version_id", sa.String(36)),
        sa.Column("workflow_version_id", sa.String(36)),
        sa.Column("model_bundle_json", sa.Text(), nullable=False),
        sa.Column("input_contract_json", sa.Text(), nullable=False),
        sa.Column("parameter_schema_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["execution_profile_id"], ["execution_profiles.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("execution_profile_id", "version_no", name="uq_execution_profile_versions_no"),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("type", sa.String(80), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("subject_type", sa.String(40), nullable=False),
        sa.Column("subject_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(200)),
        sa.Column("input_snapshot_json", sa.Text(), nullable=False),
        sa.Column("execution_profile_version_id", sa.String(36)),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_profile_version_id"], ["execution_profile_versions.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "job_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("worker_id", sa.String(100)),
        sa.Column("lease_token", sa.String(128)),
        sa.Column("lease_expires_at", sa.Text()),
        sa.Column("heartbeat_at", sa.Text()),
        sa.Column("provider_job_id", sa.String(200)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_detail_redacted", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_id", "attempt_no", name="uq_job_attempts_no"),
    )
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_attempt_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("sandbox_rel_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["job_attempt_id"], ["job_attempts.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "job_dependencies",
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("depends_on_job_id", sa.String(36), nullable=False),
        sa.PrimaryKeyConstraint("job_id", "depends_on_job_id"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["depends_on_job_id"], ["jobs.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "generation_intents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("owner_type", sa.String(40), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("creative_goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "generation_variants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("intent_id", sa.String(36), nullable=False),
        sa.Column("variant_no", sa.Integer(), nullable=False),
        sa.Column("variant_type", sa.String(40), nullable=False),
        sa.Column("parent_variant_id", sa.String(36)),
        sa.Column("branch_reason", sa.Text(), nullable=False),
        sa.Column("prompt_revision_id", sa.String(36)),
        sa.Column("capability_profile_version_id", sa.String(36), nullable=False),
        sa.Column("parameter_set_json", sa.Text(), nullable=False),
        sa.Column("seed_policy", sa.String(32), nullable=False),
        sa.Column("explicit_seed", sa.Integer()),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("recipe_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["intent_id"], ["generation_intents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_variant_id"], ["generation_variants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["capability_profile_version_id"], ["execution_profile_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("intent_id", "variant_no", name="uq_generation_variants_no"),
    )
    op.create_table(
        "variant_input_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("variant_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("media_version_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("weight", sa.Float()),
        sa.Column("frame_time_us", sa.Integer()),
        sa.Column("source_approval_id", sa.String(36)),
        sa.ForeignKeyConstraint(["variant_id"], ["generation_variants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_version_id"], ["media_versions.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "generation_experiments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("intent_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("axis_definitions_json", sa.Text(), nullable=False),
        sa.Column("cell_count", sa.Integer(), nullable=False),
        sa.Column("max_parallel", sa.Integer(), nullable=False),
        sa.Column("resource_estimate_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["intent_id"], ["generation_intents.id"], ondelete="CASCADE"),
        sa.CheckConstraint("cell_count > 0", name="ck_generation_experiments_cells"),
    )
    op.create_table(
        "experiment_cells",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("experiment_id", sa.String(36), nullable=False),
        sa.Column("cell_key", sa.String(200), nullable=False),
        sa.Column("variant_id", sa.String(36)),
        sa.Column("job_id", sa.String(36)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error_detail", sa.Text()),
        sa.ForeignKeyConstraint(["experiment_id"], ["generation_experiments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["generation_variants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("experiment_id", "cell_key", name="uq_experiment_cells_key"),
    )
    op.create_table(
        "frame_anchors",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_media_version_id", sa.String(36), nullable=False),
        sa.Column("source_time_us", sa.Integer()),
        sa.Column("source_frame_index", sa.Integer()),
        sa.Column("extracted_media_version_id", sa.String(36), nullable=False),
        sa.Column("role_hint", sa.String(40)),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("approval_id", sa.String(36)),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["source_media_version_id"], ["media_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["extracted_media_version_id"], ["media_versions.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("source_time_us IS NOT NULL OR source_frame_index IS NOT NULL", name="ck_frame_anchor_position"),
    )
    op.create_table(
        "shot_transition_constraints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("from_shot_id", sa.String(36), nullable=False),
        sa.Column("to_shot_id", sa.String(36), nullable=False),
        sa.Column("constraint_type", sa.String(48), nullable=False),
        sa.Column("from_anchor_id", sa.String(36)),
        sa.Column("to_anchor_id", sa.String(36)),
        sa.Column("enforcement", sa.String(16), nullable=False),
        sa.Column("compatibility_status", sa.String(24), nullable=False),
        sa.Column("note", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["from_shot_id"], ["shots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_shot_id"], ["shots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["from_anchor_id"], ["frame_anchors.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["to_anchor_id"], ["frame_anchors.id"], ondelete="RESTRICT"),
    )

    op.create_table(
        "timeline_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("episode_id", sa.String(36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("input_snapshot_json", sa.Text(), nullable=False),
        sa.Column("revision_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("episode_id", "revision_no", name="uq_timeline_revisions_no"),
    )
    op.create_table(
        "timeline_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("timeline_revision_id", sa.String(36), nullable=False),
        sa.Column("track_type", sa.String(32), nullable=False),
        sa.Column("media_version_id", sa.String(36)),
        sa.Column("start_us", sa.Integer(), nullable=False),
        sa.Column("end_us", sa.Integer(), nullable=False),
        sa.Column("parameters_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["timeline_revision_id"], ["timeline_revisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_version_id"], ["media_versions.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("end_us > start_us", name="ck_timeline_items_range"),
    )
    op.create_table(
        "episode_render_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("episode_id", sa.String(36), nullable=False),
        sa.Column("timeline_revision_id", sa.String(36), nullable=False),
        sa.Column("rel_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("probe_json", sa.Text(), nullable=False),
        sa.Column("integrity_status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["timeline_revision_id"], ["timeline_revisions.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "delivery_packages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("episode_render_version_id", sa.String(36), nullable=False),
        sa.Column("target_version_id", sa.String(36), nullable=False),
        sa.Column("rel_path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("manifest_sha256", sa.String(64)),
        sa.Column("withdrawn_reason", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["episode_render_version_id"], ["episode_render_versions.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "delivery_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("delivery_package_id", sa.String(36), nullable=False),
        sa.Column("rel_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["delivery_package_id"], ["delivery_packages.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("delivery_package_id", "rel_path", name="uq_delivery_files_path"),
    )

    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("role_context", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("before_revision", sa.Integer()),
        sa.Column("after_revision", sa.Integer()),
        sa.Column("request_id", sa.Text()),
        sa.Column("job_id", sa.String(36)),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("metadata_redacted_json", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "outbox_events",
        sa.Column("event_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("project_id", sa.String(36)),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("delivered_at", sa.Text()),
    )
    op.create_table(
        "command_idempotencies",
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("scope", "idempotency_key"),
    )

    for table, columns in {
        "seasons": ("project_id",),
        "episodes": ("season_id",),
        "shots": ("episode_id", "status", "order_key"),
        "media_assets": ("project_id", "owner_type", "owner_id"),
        "jobs": ("project_id", "state", "channel"),
        "generation_variants": ("intent_id", "status"),
        "outbox_events": ("delivered_at",),
    }.items():
        op.create_index(f"ix_{table}_{'_'.join(columns)}", table, list(columns))


def downgrade() -> None:
    raise RuntimeError("G2 core migration is not safely downgradeable on SQLite; restore migration preflight backup")

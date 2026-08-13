"""G3 local configuration, media indexing, import and diagnostic schema.

Revision ID: 0002_g3_config_media_import
Revises: 0001_g2_core
"""

import sqlalchemy as sa

from alembic import op

revision = "0002_g3_config_media_import"
down_revision = "0001_g2_core"
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
    op.add_column("media_assets", sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("media_versions", sa.Column("source_name", sa.Text()))
    op.add_column("media_versions", sa.Column("import_source", sa.String(32)))
    op.add_column("media_versions", sa.Column("probe_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("execution_profile_versions", sa.Column("manifest_sha256", sa.String(64)))
    op.add_column("execution_profile_versions", sa.Column("capability_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("execution_profile_versions", sa.Column("worker_policy", sa.Text()))

    op.create_table(
        "local_runtimes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(120), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("transport", sa.String(32), nullable=False),
        sa.Column("base_url", sa.Text()),
        sa.Column("executable_ref", sa.Text()),
        sa.Column("runtime_version", sa.Text()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        *_audit_columns(),
    )
    op.create_table(
        "model_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("runtime_id", sa.String(36)),
        sa.Column("code", sa.String(160), nullable=False, unique=True),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("machine_path_ref", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("license_note", sa.Text()),
        sa.Column("compatibility_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("manifest_sha256", sa.String(64)),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["runtime_id"], ["local_runtimes.id"], ondelete="SET NULL"),
    )
    op.create_table(
        "production_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(120), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        *_audit_columns(),
    )
    op.create_table(
        "production_plan_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("production_plan_id", sa.String(36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["production_plan_id"], ["production_plans.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("production_plan_id", "version_no", name="uq_production_plan_versions_no"),
    )
    op.create_table(
        "delivery_targets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("transport", sa.String(32), nullable=False),
        sa.Column("target_spec_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "code", name="uq_delivery_targets_project_code"),
    )
    op.create_table(
        "delivery_target_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("delivery_target_id", sa.String(36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("target_spec_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["delivery_target_id"], ["delivery_targets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("delivery_target_id", "version_no", name="uq_delivery_target_versions_no"),
    )
    op.create_table(
        "project_profile_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("capability", sa.String(120), nullable=False),
        sa.Column("execution_profile_version_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_profile_version_id"], ["execution_profile_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "capability", name="uq_project_profile_binding_capability"),
    )
    op.create_table(
        "project_plan_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False, unique=True),
        sa.Column("production_plan_version_id", sa.String(36), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["production_plan_version_id"], ["production_plan_versions.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "source_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("source_kind", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "code", name="uq_source_documents_project_code"),
    )
    op.create_table(
        "source_document_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_document_id", sa.String(36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("rel_path", sa.Text(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("text_sha256", sa.String(64)),
        sa.Column("extracted_text_rel", sa.Text()),
        sa.Column("parse_status", sa.String(24), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("source_document_id", "version_no", name="uq_source_document_versions_no"),
        sa.CheckConstraint("byte_size >= 0", name="ck_source_document_versions_size"),
    )
    op.create_table(
        "import_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("source_document_version_id", sa.String(36), nullable=False),
        sa.Column("session_kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("preview_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_summary", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_version_id"], ["source_document_versions.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "import_session_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("item_type", sa.String(40), nullable=False),
        sa.Column("source_start", sa.Integer()),
        sa.Column("source_end", sa.Integer()),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("validation_status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["session_id"], ["import_sessions.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "script_breakdown_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("source_document_version_id", sa.String(36), nullable=False),
        sa.Column("import_session_id", sa.String(36), nullable=False),
        sa.Column("draft_json", sa.Text(), nullable=False),
        sa.Column("confidence_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_version_id"], ["source_document_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["import_session_id"], ["import_sessions.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "media_cache_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("media_version_id", sa.String(36), nullable=False),
        sa.Column("cache_kind", sa.String(32), nullable=False),
        sa.Column("rel_path", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("preset_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["media_version_id"], ["media_versions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("media_version_id", "cache_kind", "preset_hash", name="uq_media_cache_key"),
    )
    op.create_table(
        "diagnostic_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("manifest_sha256", sa.String(64)),
        *_audit_columns(),
    )
    op.create_table(
        "diagnostic_checks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("check_code", sa.String(80), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("observed_json", sa.Text(), nullable=False),
        sa.Column("remediation_json", sa.Text(), nullable=False, server_default="{}"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["run_id"], ["diagnostic_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "check_code", name="uq_diagnostic_checks_code"),
    )

    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS fts_search USING fts5(project_id UNINDEXED, subject_type UNINDEXED, subject_id UNINDEXED, content)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_media_versions_sha256 ON media_versions(sha256)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_source_document_versions_sha256 ON source_document_versions(sha256)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_import_sessions_project_status ON import_sessions(project_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_diagnostic_checks_status ON diagnostic_checks(status)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_diagnostic_checks_status")
    op.execute("DROP INDEX IF EXISTS ix_import_sessions_project_status")
    op.execute("DROP INDEX IF EXISTS ix_source_document_versions_sha256")
    op.execute("DROP INDEX IF EXISTS ix_media_versions_sha256")
    op.execute("DROP TABLE IF EXISTS fts_search")
    for table in (
        "diagnostic_checks",
        "diagnostic_runs",
        "media_cache_entries",
        "script_breakdown_drafts",
        "import_session_items",
        "import_sessions",
        "source_document_versions",
        "source_documents",
        "project_plan_bindings",
        "project_profile_bindings",
        "delivery_target_versions",
        "delivery_targets",
        "production_plan_versions",
        "production_plans",
        "model_artifacts",
        "local_runtimes",
    ):
        op.drop_table(table)
    op.drop_column("execution_profile_versions", "worker_policy")
    op.drop_column("execution_profile_versions", "capability_json")
    op.drop_column("execution_profile_versions", "manifest_sha256")
    op.drop_column("media_versions", "probe_json")
    op.drop_column("media_versions", "import_source")
    op.drop_column("media_versions", "source_name")
    op.drop_column("media_assets", "metadata_json")

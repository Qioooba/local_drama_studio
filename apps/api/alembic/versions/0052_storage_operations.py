"""Staged filesystem operation ledger and quarantine facts (PR-CUR-011)."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0052_storage_operations"
down_revision = "0051_worker_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "storage_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("operation_type", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("source_name", sa.String(240), nullable=False, server_default=""),
        sa.Column("staging_rel_path", sa.Text(), nullable=True),
        sa.Column("destination_rel_path", sa.Text(), nullable=True),
        sa.Column("quarantine_rel_path", sa.Text(), nullable=True),
        sa.Column("expected_sha256", sa.String(64), nullable=True),
        sa.Column("expected_byte_size", sa.Integer(), nullable=True),
        sa.Column("actual_sha256", sa.String(64), nullable=True),
        sa.Column("actual_byte_size", sa.Integer(), nullable=True),
        sa.Column("request_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("media_asset_id", sa.String(36), nullable=True),
        sa.Column("media_version_id", sa.String(36), nullable=True),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("last_error_redacted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("finalized_at", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["media_asset_id"], ["media_assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["media_version_id"], ["media_versions.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("idempotency_key", name="uq_storage_operations_idempotency"),
        sa.CheckConstraint(
            "status IN ('STAGING','STAGED','FINALIZING','FILE_COMMITTED','COMMITTED','NEEDS_ATTENTION','FAILED','QUARANTINED')",
            name="ck_storage_operations_status",
        ),
    )
    op.create_index("ix_storage_operations_status_updated", "storage_operations", ["status", "updated_at"])
    op.create_index("ix_storage_operations_project_created", "storage_operations", ["project_id", "created_at"])
    op.create_index("ix_storage_operations_media_version", "storage_operations", ["media_version_id"])


def downgrade() -> None:
    op.drop_index("ix_storage_operations_media_version", table_name="storage_operations")
    op.drop_index("ix_storage_operations_project_created", table_name="storage_operations")
    op.drop_index("ix_storage_operations_status_updated", table_name="storage_operations")
    op.drop_table("storage_operations")

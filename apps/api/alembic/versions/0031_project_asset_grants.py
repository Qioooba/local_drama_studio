"""Add immutable cross-project workspace asset grants for FR-AST-001."""

import sqlalchemy as sa

from alembic import op

revision = "0031_project_asset_grants"
down_revision = "0030_versioned_post_process_chain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_asset_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("target_project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("source_authorization_id", sa.String(36), sa.ForeignKey("workspace_asset_authorizations.id"), nullable=False),
        sa.Column("media_version_id", sa.String(36), sa.ForeignKey("media_versions.id"), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("source_byte_size", sa.Integer(), nullable=False),
        sa.Column("access_mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("withdrawal_reason", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.UniqueConstraint("target_project_id", "media_version_id", name="uq_project_asset_grant_target_media"),
    )
    op.create_index("ix_project_asset_grants_target_status", "project_asset_grants", ["target_project_id", "status"])
    op.create_index("ix_project_asset_grants_source_media", "project_asset_grants", ["source_project_id", "media_version_id"])


def downgrade() -> None:
    raise RuntimeError("Project asset grants are immutable release evidence; restore migration preflight backup")

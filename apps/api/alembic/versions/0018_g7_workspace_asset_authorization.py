"""Persist G7 workspace asset authorization and BrandKit versions."""

import sqlalchemy as sa

from alembic import op

revision = "0018_g7_workspace_asset_authorization"
down_revision = "0017_g7_zero_public_network_e2e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_asset_authorizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("media_version_id", sa.String(36), sa.ForeignKey("media_versions.id"), nullable=False),
        sa.Column("asset_kind", sa.String(32), nullable=False),
        sa.Column("path_rel", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("authorization_status", sa.String(24), nullable=False),
        sa.Column("license_status", sa.String(32), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v2"),
        sa.UniqueConstraint("project_id", "media_version_id", name="uq_workspace_asset_auth_project_media"),
    )
    op.create_index(
        "ix_workspace_asset_authorizations_project_status",
        "workspace_asset_authorizations",
        ["project_id", "authorization_status"],
    )
    op.create_table(
        "brand_kits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("tokens_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v2"),
        sa.UniqueConstraint("project_id", "code", "version_no", name="uq_brand_kits_project_code_version"),
    )
    op.create_index("ix_brand_kits_project_status", "brand_kits", ["project_id", "status"])


def downgrade() -> None:
    raise RuntimeError("Workspace authorization is release evidence; restore migration preflight backup")

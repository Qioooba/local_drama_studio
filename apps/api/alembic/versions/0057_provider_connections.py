"""Generic local provider connection metadata; secrets stay in Credential Manager.

Revision ID: 0057_provider_connections
Revises: 0056_breakdown_draft_revisions
"""

import sqlalchemy as sa

from alembic import op


revision = "0057_provider_connections"
down_revision = "0056_breakdown_draft_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_connections",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("code", sa.String(120), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("provider_kind", sa.String(48), nullable=False),
        sa.Column("protocol", sa.String(48), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("model", sa.String(240)),
        sa.Column("credential_source", sa.String(48), nullable=False, server_default="NONE"),
        sa.Column("credential_ref", sa.String(240)),
        sa.Column("environment_variable_name", sa.String(160)),
        sa.Column("status", sa.String(24), nullable=False, server_default="ACTIVE"),
        sa.Column("last_probe_status", sa.String(32)),
        sa.Column("last_probe_at", sa.Text()),
        sa.Column("last_probe_summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.Text(), nullable=False, server_default="system"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
    )
    op.create_index("ix_provider_connections_kind_status", "provider_connections", ["provider_kind", "status"])


def downgrade() -> None:
    op.drop_index("ix_provider_connections_kind_status", table_name="provider_connections")
    op.drop_table("provider_connections")

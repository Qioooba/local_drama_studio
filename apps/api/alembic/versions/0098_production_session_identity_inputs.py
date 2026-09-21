"""Add session-scoped temporary character identity inputs."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0098_production_session_identity_inputs"
down_revision = "0097_video_upscale_previews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "production_session_identity_inputs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("story_asset_id", sa.String(36), nullable=False),
        sa.Column("pack_version_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by", sa.Text(), nullable=False, server_default=sa.text("'system'")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "schema_version",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'production-identity-input.v1'"),
        ),
        sa.ForeignKeyConstraint(["session_id"], ["production_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["story_asset_id"], ["story_assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["pack_version_id"], ["character_identity_pack_versions.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "state IN ('ACTIVE','SUPERSEDED','REVOKED')",
            name="ck_production_session_identity_inputs_state",
        ),
    )
    op.create_index(
        "uq_production_session_identity_inputs_active",
        "production_session_identity_inputs",
        ["session_id", "story_asset_id"],
        unique=True,
        sqlite_where=sa.text("state='ACTIVE'"),
    )
    op.create_index(
        "ix_production_session_identity_inputs_pack",
        "production_session_identity_inputs",
        ["pack_version_id", "state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_production_session_identity_inputs_pack",
        table_name="production_session_identity_inputs",
    )
    op.drop_index(
        "uq_production_session_identity_inputs_active",
        table_name="production_session_identity_inputs",
    )
    op.drop_table("production_session_identity_inputs")

"""Add session-scoped machine-temporary asset identity inputs."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0099_production_session_asset_inputs"
down_revision = "0098_production_session_identity_inputs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "production_session_asset_inputs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("session_item_id", sa.String(36), nullable=False),
        sa.Column("episode_id", sa.String(36), nullable=False),
        sa.Column("asset_proposal_id", sa.String(36), nullable=False),
        sa.Column("story_asset_id", sa.String(36), nullable=False),
        sa.Column(
            "state", sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'")
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "schema_version",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'production-session-asset-input.v1'"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["production_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["session_item_id"], ["production_session_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["asset_proposal_id"], ["story_asset_proposals.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["story_asset_id"], ["story_assets.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "session_id",
            "asset_proposal_id",
            name="uq_production_session_asset_input_proposal",
        ),
        sa.CheckConstraint(
            "state IN ('ACTIVE','REVOKED')",
            name="ck_production_session_asset_input_state",
        ),
    )
    op.create_index(
        "ix_production_session_asset_inputs_item",
        "production_session_asset_inputs",
        ["session_item_id", "state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_production_session_asset_inputs_item",
        table_name="production_session_asset_inputs",
    )
    op.drop_table("production_session_asset_inputs")

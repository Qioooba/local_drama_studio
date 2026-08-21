"""Reviewable AI-extracted Story Asset identity proposals."""

import sqlalchemy as sa

from alembic import op

revision = "0048_asset_proposals"
down_revision = "0047_shot_editing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "story_asset_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("breakdown_draft_id", sa.String(36), nullable=True),
        sa.Column("proposal_key", sa.String(240), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("suggested_asset_id", sa.String(36), nullable=True),
        sa.Column("resolved_asset_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("decision_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v2"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["breakdown_draft_id"], ["script_breakdown_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["suggested_asset_id"], ["story_assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_asset_id"], ["story_assets.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("breakdown_draft_id", "proposal_key", name="uq_asset_proposal_draft_key"),
    )
    op.create_index("ix_asset_proposals_project_status", "story_asset_proposals", ["project_id", "status"])


def downgrade() -> None:
    raise RuntimeError("Asset proposal decisions are append-only; restore the pre-migration backup")

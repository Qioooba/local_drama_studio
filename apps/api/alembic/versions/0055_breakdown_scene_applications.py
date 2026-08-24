"""Track atomic, idempotent scene-level script-breakdown application."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0055_breakdown_scene_applications"
down_revision = "0054_character_identity_pack_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "script_breakdown_scene_applications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("breakdown_draft_id", sa.String(36), nullable=False),
        sa.Column("episode_id", sa.String(36), nullable=False),
        sa.Column("scene_no", sa.Integer(), nullable=False),
        sa.Column("created_scene_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["breakdown_draft_id"], ["script_breakdown_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_scene_id"], ["scenes.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("breakdown_draft_id", "scene_no", name="uq_breakdown_scene_application"),
    )
    op.create_index(
        "ix_breakdown_scene_applications_episode",
        "script_breakdown_scene_applications",
        ["episode_id", "scene_no"],
    )


def downgrade() -> None:
    raise RuntimeError("Scene-level breakdown application facts are append-only; restore the pre-migration backup")

"""Track stale FrameAnchors when an approved source-video winner changes."""

import sqlalchemy as sa

from alembic import op

revision = "0010_g6_frame_anchor_stale"
down_revision = "0009_g6_continuity_stale"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("frame_anchors", sa.Column("is_stale", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("frame_anchors", sa.Column("stale_reason", sa.Text()))
    op.create_index("ix_frame_anchors_stale", "frame_anchors", ["is_stale", "source_media_version_id"])


def downgrade() -> None:
    raise RuntimeError("FrameAnchor stale state is production history; restore migration preflight backup")

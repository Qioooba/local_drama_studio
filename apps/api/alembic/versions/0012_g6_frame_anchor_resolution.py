"""Freeze requested and resolved positions for exact FrameAnchor extraction."""

import sqlalchemy as sa

from alembic import op

revision = "0012_g6_frame_anchor_resolution"
down_revision = "0011_g6_provider_random_nonce"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("frame_anchors", sa.Column("requested_time_us", sa.Integer()))
    op.add_column("frame_anchors", sa.Column("resolved_time_us", sa.Integer()))
    op.add_column("frame_anchors", sa.Column("source_sha256", sa.String(64)))
    op.add_column("frame_anchors", sa.Column("extraction_method", sa.String(40)))


def downgrade() -> None:
    raise RuntimeError("Resolved FrameAnchor evidence is immutable production history; restore migration preflight backup")

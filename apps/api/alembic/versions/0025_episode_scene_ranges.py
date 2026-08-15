"""Map project-level master scenes to episode source ranges without duplication."""

import sqlalchemy as sa

from alembic import op

revision = "0025_episode_scene_ranges"
down_revision = "0024_video_review_annotations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "episode_scene_ranges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("episode_id", sa.String(36), sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scene_id", sa.String(36), sa.ForeignKey("scenes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_start", sa.Integer(), nullable=False),
        sa.Column("source_end", sa.Integer(), nullable=False),
        sa.Column("source_label", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.String(16), nullable=False, server_default="v2"),
        sa.CheckConstraint("ordinal >= 1", name="ck_episode_scene_range_ordinal"),
        sa.CheckConstraint("source_start >= 0 AND source_end > source_start", name="ck_episode_scene_range_offsets"),
        sa.UniqueConstraint("episode_id", "scene_id", name="uq_episode_scene_range_scene"),
        sa.UniqueConstraint("episode_id", "ordinal", name="uq_episode_scene_range_ordinal"),
    )
    op.create_index("ix_episode_scene_ranges_episode_order", "episode_scene_ranges", ["episode_id", "ordinal"])
    op.create_index("ix_episode_scene_ranges_scene", "episode_scene_ranges", ["scene_id"])


def downgrade() -> None:
    raise RuntimeError("Episode scene ranges are narrative identity evidence; restore migration preflight backup")

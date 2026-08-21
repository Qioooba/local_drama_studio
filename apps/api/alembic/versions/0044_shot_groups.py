"""Semantic shot scene ownership and episode-local shot groups."""

import sqlalchemy as sa

from alembic import op

revision = "0044_shot_groups"
down_revision = "0043_generation_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable and deliberately not guessed: existing shots keep NULL until a
    # human-approved breakdown can identify their semantic scene.
    # SQLite cannot ALTER an existing table with a new FK constraint without
    # rebuilding the full historical shots table. Keep this additive and
    # nullable; command-layer scope validation owns the same-project invariant.
    op.add_column("shots", sa.Column("scene_id", sa.String(36), nullable=True))
    op.create_index("ix_shots_scene_order", "shots", ["scene_id", "order_key"])
    op.create_table(
        "shot_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("episode_id", sa.String(36), nullable=False),
        sa.Column("scene_id", sa.String(36), nullable=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("order_key", sa.String(64), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("episode_id", "code", name="uq_shot_groups_episode_code"),
        sa.CheckConstraint("kind IN ('BEAT','DIALOGUE','ACTION','MONTAGE','CUSTOM')", name="ck_shot_groups_kind"),
        sa.CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="ck_shot_groups_status"),
    )
    op.create_index("ix_shot_groups_episode_order", "shot_groups", ["episode_id", "order_key"])
    op.create_index("ix_shot_groups_scene", "shot_groups", ["scene_id"])
    op.create_table(
        "shot_group_members",
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("shot_id", sa.String(36), nullable=False),
        sa.Column("order_key", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["shot_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shot_id"], ["shots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("group_id", "shot_id"),
    )
    op.create_index("ix_shot_group_members_shot", "shot_group_members", ["shot_id"])
    op.create_index("ix_shot_group_members_order", "shot_group_members", ["group_id", "order_key"])


def downgrade() -> None:
    raise RuntimeError("Shot grouping is production history; restore pre-migration backup")

"""Add durable shot split lineage and non-destructive archival."""

import sqlalchemy as sa

from alembic import op

revision = "0047_shot_editing"
down_revision = "0046_director_recipes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain nullable lineage column keeps SQLite ALTER additive. Scope and
    # existence are enforced by the shot-edit command transaction.
    op.add_column("shots", sa.Column("source_shot_id", sa.String(36), nullable=True))
    op.add_column("shots", sa.Column("archived_at", sa.Text(), nullable=True))
    op.create_index("ix_shots_source_shot", "shots", ["source_shot_id"])
    op.create_index("ix_shots_episode_archived_order", "shots", ["episode_id", "archived_at", "order_key"])


def downgrade() -> None:
    raise RuntimeError("Shot split lineage is production history; restore pre-migration backup")

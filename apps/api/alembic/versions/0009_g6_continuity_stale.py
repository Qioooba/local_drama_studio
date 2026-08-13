"""G6/G8 continuity revision snapshots and transactional stale propagation."""

import sqlalchemy as sa

from alembic import op

revision = "0009_g6_continuity_stale"
down_revision = "0008_g6_prompt_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shot_transition_constraints", sa.Column("boundary_revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("shot_transition_constraints", sa.Column("is_stale", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("shot_transition_constraints", sa.Column("stale_reason", sa.Text()))
    op.add_column("generation_variants", sa.Column("is_stale", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("generation_variants", sa.Column("stale_reason", sa.Text()))
    op.create_index("ix_transition_stale", "shot_transition_constraints", ["is_stale", "enforcement"])
    op.create_index("ix_generation_variants_stale", "generation_variants", ["is_stale", "status"])


def downgrade() -> None:
    raise RuntimeError("Continuity stale state is production history; restore migration preflight backup")

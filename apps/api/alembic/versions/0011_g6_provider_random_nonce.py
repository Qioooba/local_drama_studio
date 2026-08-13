"""Freeze provider-random nonce in GenerationVariant snapshots."""

import sqlalchemy as sa

from alembic import op

revision = "0011_g6_provider_random_nonce"
down_revision = "0010_g6_frame_anchor_stale"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generation_variants", sa.Column("provider_random_nonce", sa.String(36)))
    op.create_index("ix_generation_variants_provider_random_nonce", "generation_variants", ["provider_random_nonce"], unique=True)


def downgrade() -> None:
    raise RuntimeError("Provider-random nonce is immutable production history; restore migration preflight backup")

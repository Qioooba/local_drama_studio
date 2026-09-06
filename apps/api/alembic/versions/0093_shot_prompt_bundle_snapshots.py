"""Freeze prompt bundles in recoverable keyframe and generation jobs.

Revision ID: 0093_shot_prompt_bundle_snapshots
Revises: 0092_shot_keyframe_generation_batches
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0093_shot_prompt_bundle_snapshots"
down_revision = "0092_shot_keyframe_generation_batches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shot_keyframe_generation_batches",
        sa.Column("input_snapshot_json", sa.Text(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "shot_keyframe_generation_batch_items",
        sa.Column("input_snapshot_json", sa.Text(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("shot_keyframe_generation_batch_items", "input_snapshot_json")
    op.drop_column("shot_keyframe_generation_batches", "input_snapshot_json")


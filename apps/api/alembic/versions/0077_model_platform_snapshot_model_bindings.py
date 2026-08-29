"""Freeze concrete model bindings with every V2 execution snapshot.

Revision ID: 0077_model_platform_snapshot_model_bindings
Revises: 0076_model_platform_snapshot_runtime_configuration
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0077_model_platform_snapshot_model_bindings"
down_revision = "0076_model_platform_snapshot_runtime_configuration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mp_execution_snapshots",
        sa.Column("model_bindings_json", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("mp_execution_snapshots", "model_bindings_json")

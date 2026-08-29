"""Freeze safe runtime configuration with every V2 execution snapshot.

Revision ID: 0076_model_platform_snapshot_runtime_configuration
Revises: 0075_model_platform_adaptation_execution_merge
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0076_model_platform_snapshot_runtime_configuration"
down_revision = "0075_model_platform_adaptation_execution_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mp_execution_snapshots",
        sa.Column("runtime_configuration_json", sa.Text(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("mp_execution_snapshots", "runtime_configuration_json")

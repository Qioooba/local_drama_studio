"""Freeze Profile-owned execution binding details in every V2 snapshot.

Revision ID: 0080_model_platform_snapshot_execution_binding
Revises: 0079_model_platform_comfy_smoke_jobs
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0080_model_platform_snapshot_execution_binding"
down_revision = "0079_model_platform_comfy_smoke_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mp_execution_snapshots",
        sa.Column("execution_binding_json", sa.Text(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("mp_execution_snapshots", "execution_binding_json")

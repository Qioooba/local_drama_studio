"""Add reversible task-list deletion metadata.

Revision ID: 0069_job_history_deletion
Revises: 0068_local_ai_model_runtime
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0069_job_history_deletion"
down_revision = "0068_local_ai_model_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("deleted_at", sa.Text(), nullable=True))
    op.create_index("ix_jobs_deleted_at", "jobs", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_deleted_at", table_name="jobs")
    op.drop_column("jobs", "deleted_at")

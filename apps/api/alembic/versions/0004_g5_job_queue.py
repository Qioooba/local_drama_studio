"""G5 persistent job queue, retry and lease indexes."""

import sqlalchemy as sa

from alembic import op

revision = "0004_g5_job_queue"
down_revision = "0003_g4_review_selection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("priority", sa.Integer(), nullable=False, server_default="100"))
    op.add_column("jobs", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("jobs", sa.Column("next_run_at", sa.Text()))
    op.add_column("jobs", sa.Column("cancel_requested_at", sa.Text()))
    op.add_column("jobs", sa.Column("last_error_code", sa.String(80)))
    op.create_index("ix_jobs_queue_eligible", "jobs", ["state", "channel", "priority", "next_run_at"])
    op.create_index("ix_job_attempts_lease", "job_attempts", ["state", "lease_expires_at"])


def downgrade() -> None:
    raise RuntimeError("G5 migration is not safely downgradeable on SQLite; restore migration preflight backup")

"""Persist queue progress and scheduler resource leases for G5 jobs."""

import sqlalchemy as sa

from alembic import op

revision = "0037_job_progress_scheduler"
down_revision = "0036_motion_control_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Progress is part of the durable job read model, not an ephemeral SSE-only
    # value. Keeping it on jobs means a restarted API/browser can render the
    # latest phase/node/percentage without contacting a worker.
    op.add_column("jobs", sa.Column("progress_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("jobs", sa.Column("progress_updated_at", sa.Text()))
    op.add_column("jobs", sa.Column("started_at", sa.Text()))
    op.add_column("jobs", sa.Column("finished_at", sa.Text()))
    op.add_column("jobs", sa.Column("last_error_detail_redacted", sa.Text()))
    op.add_column("job_attempts", sa.Column("progress_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("job_attempts", sa.Column("started_at", sa.Text()))
    op.add_column("job_attempts", sa.Column("finished_at", sa.Text()))
    op.create_table(
        "job_resource_leases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("resource_key", sa.String(80), nullable=False),
        sa.Column("acquired_at", sa.Text(), nullable=False),
        sa.Column("released_at", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="scheduler"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attempt_id"], ["job_attempts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("attempt_id", "resource_key", name="uq_job_resource_lease_attempt"),
    )
    op.create_index("ix_job_resource_leases_active", "job_resource_leases", ["resource_key", "released_at"])


def downgrade() -> None:
    raise RuntimeError("Job progress is release history; restore migration preflight backup")

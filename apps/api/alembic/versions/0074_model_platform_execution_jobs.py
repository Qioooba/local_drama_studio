"""Link durable Jobs to immutable V2 execution snapshots.

Revision ID: 0074_model_platform_execution_jobs
Revises: 0073_model_platform_execution_snapshots
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0074_model_platform_execution_jobs"
down_revision = "0073_model_platform_execution_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mp_execution_job_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column(
            "execution_snapshot_id",
            sa.String(36),
            sa.ForeignKey("mp_execution_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("handler_code", sa.String(120), nullable=False),
        sa.Column("handler_version", sa.String(80), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_mp_execution_job_links_snapshot", "mp_execution_job_links", ["execution_snapshot_id"])
    op.execute(
        """INSERT INTO job_stage_definitions(code,title,domain,active)
           VALUES ('MODEL_PLATFORM_EXECUTION','模型平台执行','SYSTEM',1)
           ON CONFLICT(code) DO UPDATE SET title=excluded.title, domain=excluded.domain, active=1"""
    )


def downgrade() -> None:
    op.execute("DELETE FROM job_stage_definitions WHERE code='MODEL_PLATFORM_EXECUTION'")
    op.drop_index("ix_mp_execution_job_links_snapshot", table_name="mp_execution_job_links")
    op.drop_table("mp_execution_job_links")

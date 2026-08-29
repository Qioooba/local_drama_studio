"""Register the durable Job stage used by adaptation analysis runs.

Revision ID: 0072_adaptation_analysis_job_stage
Revises: 0071_model_platform_adaptation_merge
"""

from __future__ import annotations

from alembic import op

revision = "0072_adaptation_analysis_job_stage"
down_revision = "0071_model_platform_adaptation_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """INSERT INTO job_stage_definitions(code,title,domain,active)
           VALUES ('ADAPTATION_ANALYSIS','长篇改编分层分析','ADAPTATION',1)
           ON CONFLICT(code) DO UPDATE SET title=excluded.title, domain=excluded.domain, active=1"""
    )


def downgrade() -> None:
    op.execute("DELETE FROM job_stage_definitions WHERE code='ADAPTATION_ANALYSIS'")

"""Merge adaptation materialization with V2 model-platform execution links.

Revision ID: 0075_model_platform_adaptation_execution_merge
Revises: 0073_adaptation_plan_materialization, 0074_model_platform_execution_jobs
"""

from __future__ import annotations

revision = "0075_model_platform_adaptation_execution_merge"
down_revision = ("0073_adaptation_plan_materialization", "0074_model_platform_execution_jobs")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

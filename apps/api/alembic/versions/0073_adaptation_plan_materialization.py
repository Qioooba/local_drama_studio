"""Track explicit publication of approved adaptation plans.

Revision ID: 0073_adaptation_plan_materialization
Revises: 0072_adaptation_analysis_job_stage
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0073_adaptation_plan_materialization"
down_revision = "0072_adaptation_analysis_job_stage"
branch_labels = None
depends_on = None


def _audit_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by", sa.Text(), nullable=False, server_default=sa.text("'system'")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default=sa.text("'v1'")),
    )


def upgrade() -> None:
    op.create_table(
        "adaptation_plan_materializations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_id", sa.String(36), nullable=False),
        sa.Column("plan_revision_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["plan_id"], ["adaptation_plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plan_revision_id"], ["adaptation_plan_revisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("strategy='APPEND_NEW'", name="ck_adaptation_materialization_strategy"),
        sa.UniqueConstraint("plan_revision_id", name="uq_adaptation_materialization_revision"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_adaptation_materialization_idempotency"),
    )
    op.create_table(
        "adaptation_materialized_episode_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("materialization_id", sa.String(36), nullable=False),
        sa.Column("plan_episode_id", sa.String(36), nullable=False),
        sa.Column("season_id", sa.String(36), nullable=False),
        sa.Column("episode_id", sa.String(36), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["materialization_id"], ["adaptation_plan_materializations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_episode_id"], ["adaptation_episode_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("materialization_id", "plan_episode_id", name="uq_adaptation_materialized_episode"),
        sa.UniqueConstraint("episode_id", name="uq_adaptation_materialized_physical_episode"),
    )


def downgrade() -> None:
    op.drop_table("adaptation_materialized_episode_links")
    op.drop_table("adaptation_plan_materializations")

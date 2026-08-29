"""Persist V2-only multi-stage Quick Create execution lineage.

Revision ID: 0086_model_platform_quick_create_v2_runs
Revises: 0085_model_platform_project_knowledge_retry_attempts
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0086_model_platform_quick_create_v2_runs"
down_revision = "0085_model_platform_project_knowledge_retry_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mp_quick_create_v2_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column("mode", sa.String(40), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("source_prompt_json", sa.Text(), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("selected_step_id", sa.String(36), nullable=True),
        sa.Column("final_step_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("mode IN ('TEXT_TO_VIDEO','TEXT_TO_IMAGE_TO_VIDEO')", name="ck_mp_quick_create_v2_run_mode"),
        sa.UniqueConstraint("mode", "input_hash", name="uq_mp_quick_create_v2_run_input"),
    )
    op.create_table(
        "mp_quick_create_v2_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mp_quick_create_v2_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column("step_kind", sa.String(40), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("capability_code", sa.String(80), nullable=False),
        sa.Column("execution_snapshot_id", sa.String(36), sa.ForeignKey("mp_execution_snapshots.id", ondelete="RESTRICT"), nullable=True, unique=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=True, unique=True),
        sa.Column("input_artifact_id", sa.String(36), sa.ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("output_artifact_id", sa.String(36), sa.ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=True, unique=True),
        sa.Column("selection_rank", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_detail_redacted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("run_id", "step_no", name="uq_mp_quick_create_v2_step_order"),
        sa.CheckConstraint("step_no >= 1", name="ck_mp_quick_create_v2_step_no"),
    )
    op.create_index("ix_mp_quick_create_v2_runs_state_updated", "mp_quick_create_v2_runs", ["state", "updated_at"])
    op.create_index("ix_mp_quick_create_v2_steps_run_state", "mp_quick_create_v2_steps", ["run_id", "state", "step_no"])


def downgrade() -> None:
    op.drop_index("ix_mp_quick_create_v2_steps_run_state", table_name="mp_quick_create_v2_steps")
    op.drop_index("ix_mp_quick_create_v2_runs_state_updated", table_name="mp_quick_create_v2_runs")
    op.drop_table("mp_quick_create_v2_steps")
    op.drop_table("mp_quick_create_v2_runs")

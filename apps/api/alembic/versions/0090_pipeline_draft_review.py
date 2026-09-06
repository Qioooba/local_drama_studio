"""Draft-first story pipeline review and apply state.

Revision ID: 0090_pipeline_draft_review
Revises: 0089_pipeline_llm_mode
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0090_pipeline_draft_review"
down_revision = "0089_pipeline_llm_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """INSERT INTO job_stage_definitions(code,title,domain,active)
        VALUES ('STORY_PIPELINE','故事规划草案','STORY_PIPELINE',1)"""
    )
    with op.batch_alter_table("pipeline_runs", recreate="always") as batch:
        batch.add_column(sa.Column("job_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("source_label", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("input_snapshot_json", sa.Text(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("draft_json", sa.Text(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("quality_report_json", sa.Text(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("apply_state", sa.String(24), nullable=False, server_default="NOT_APPLIED"))
        batch.add_column(sa.Column("applied_sections_json", sa.Text(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("applied_at", sa.Text(), nullable=True))
        batch.add_column(sa.Column("supersedes_run_id", sa.String(36), nullable=True))
        batch.create_check_constraint(
            "ck_pipeline_runs_apply_state",
            "apply_state IN ('NOT_APPLIED','APPLIED')",
        )
        batch.create_foreign_key(
            "fk_pipeline_runs_job_id",
            "jobs",
            ["job_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_pipeline_runs_supersedes",
            "pipeline_runs",
            ["supersedes_run_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("pipeline_runs", recreate="always") as batch:
        batch.drop_constraint("fk_pipeline_runs_supersedes", type_="foreignkey")
        batch.drop_constraint("fk_pipeline_runs_job_id", type_="foreignkey")
        batch.drop_constraint("ck_pipeline_runs_apply_state", type_="check")
        batch.drop_column("supersedes_run_id")
        batch.drop_column("applied_at")
        batch.drop_column("applied_sections_json")
        batch.drop_column("apply_state")
        batch.drop_column("quality_report_json")
        batch.drop_column("draft_json")
        batch.drop_column("input_snapshot_json")
        batch.drop_column("source_label")
        batch.drop_column("job_id")
    op.execute("DELETE FROM job_stage_definitions WHERE code='STORY_PIPELINE'")

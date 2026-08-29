"""Link durable V2 Comfy capability smoke Jobs to frozen workflow bindings.

Revision ID: 0079_model_platform_comfy_smoke_jobs
Revises: 0078_model_platform_comfy_workflow_bindings
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0079_model_platform_comfy_smoke_jobs"
down_revision = "0078_model_platform_comfy_workflow_bindings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mp_comfy_capability_smoke_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("workflow_binding_id", sa.String(36), sa.ForeignKey("mp_runtime_model_workflow_bindings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("runtime_model_installation_id", sa.String(36), sa.ForeignKey("mp_runtime_model_installations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("capability_definition_id", sa.String(36), sa.ForeignKey("mp_capability_definitions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("workflow_version_id", sa.String(36), nullable=False),
        sa.Column("workflow_content_hash", sa.String(64), nullable=False),
        sa.Column("smoke_contract_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_mp_comfy_capability_smoke_jobs_binding",
        "mp_comfy_capability_smoke_jobs",
        ["workflow_binding_id", "created_at"],
    )
    op.execute(
        """INSERT INTO job_stage_definitions(code,title,domain,active)
           VALUES ('MODEL_PLATFORM_COMFY_SMOKE','Comfy 能力冒烟','SYSTEM',1)
           ON CONFLICT(code) DO UPDATE SET title=excluded.title,domain=excluded.domain,active=1"""
    )


def downgrade() -> None:
    op.execute("DELETE FROM job_stage_definitions WHERE code='MODEL_PLATFORM_COMFY_SMOKE'")
    op.drop_index("ix_mp_comfy_capability_smoke_jobs_binding", table_name="mp_comfy_capability_smoke_jobs")
    op.drop_table("mp_comfy_capability_smoke_jobs")

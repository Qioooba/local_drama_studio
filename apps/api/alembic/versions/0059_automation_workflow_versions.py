"""Turn automation workflow rows into immutable project-scoped versions.

Revision ID: 0059_automation_workflow_versions
Revises: 0058_one_sentence_video_runs
"""

import sqlalchemy as sa

from alembic import op

revision = "0059_automation_workflow_versions"
down_revision = "0058_one_sentence_video_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("automation_workflows", recreate="always") as batch:
        batch.drop_constraint("uq_automation_workflows_project_code", type_="unique")
        batch.add_column(sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("template_code", sa.String(120), nullable=True))
        batch.add_column(sa.Column("source_fingerprint", sa.String(64), nullable=True))
        batch.create_unique_constraint("uq_automation_workflows_project_code_version", ["project_id", "code", "version_no"])
    op.create_index("ix_automation_workflows_project_code_version", "automation_workflows", ["project_id", "code", "version_no"])


def downgrade() -> None:
    raise RuntimeError("Automation workflow versions are referenced by immutable runs; restore the pre-upgrade backup")

"""Pipeline LLM extraction mode visibility.

Revision ID: 0089_pipeline_llm_mode
Revises: 0088_pipeline_runs
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0089_pipeline_llm_mode"
down_revision = "0088_pipeline_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("pipeline_runs", recreate="always") as batch:
        batch.add_column(sa.Column("extraction_mode", sa.Text(), nullable=False, server_default="UNKNOWN"))
        batch.add_column(sa.Column("llm_model", sa.Text(), nullable=True))
        batch.add_column(sa.Column("llm_provider", sa.Text(), nullable=True))
        batch.add_column(sa.Column("llm_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("pipeline_runs", recreate="always") as batch:
        batch.drop_column("llm_error")
        batch.drop_column("llm_provider")
        batch.drop_column("llm_model")
        batch.drop_column("extraction_mode")

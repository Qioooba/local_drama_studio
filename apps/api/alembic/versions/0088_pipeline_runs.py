"""Pipeline run persistence for one-click story & bible.

Revision ID: 0088_pipeline_runs
Revises: 0087_gpu_runtime_llama_cpp
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0088_pipeline_runs"
down_revision = "0087_gpu_runtime_llama_cpp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("stage_label", sa.Text(), nullable=False, server_default=""),
        sa.Column("progress_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("visual_style", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "target_episode_duration_seconds",
            sa.Integer(),
            nullable=False,
            server_default="120",
        ),
        sa.Column("voice_preset", sa.Text(), nullable=False, server_default="DEFAULT_VOX_CPM2"),
        sa.Column("source_document_version_id", sa.String(36), nullable=True),
        sa.Column("capability_profile_version_id", sa.String(36), nullable=True),
        sa.Column("episodes_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("characters_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scenes_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("props_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shots_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("episodes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column(
            "assets_json",
            sa.Text(),
            nullable=False,
            server_default='{"characters":[],"scenes":[],"props":[]}',
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v2"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "progress_pct >= 0 AND progress_pct <= 100",
            name="ck_pipeline_runs_progress",
        ),
        sa.CheckConstraint(
            "state IN ('RUNNING','PAUSED','SUCCEEDED','FAILED','CANCELLED')",
            name="ck_pipeline_runs_state",
        ),
    )
    op.create_index(
        "ix_pipeline_runs_project_state_created",
        "pipeline_runs",
        ["project_id", "state", "created_at"],
    )
    op.create_index(
        "ix_pipeline_runs_project_created",
        "pipeline_runs",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_project_created", table_name="pipeline_runs")
    op.drop_index("ix_pipeline_runs_project_state_created", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")

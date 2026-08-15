"""Persist deterministic episode render inputs and local FFmpeg execution evidence."""

import sqlalchemy as sa

from alembic import op

revision = "0032_episode_render_execution_evidence"
down_revision = "0031_project_asset_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("episode_render_versions", sa.Column("input_snapshot_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("episode_render_versions", sa.Column("ffmpeg_command_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("episode_render_versions", sa.Column("execution_log_text", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    raise RuntimeError("Episode render evidence is immutable release history; restore migration preflight backup")

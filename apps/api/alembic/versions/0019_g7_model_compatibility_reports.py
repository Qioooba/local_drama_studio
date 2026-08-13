"""Persist offline model license, hash and quantization compatibility reports."""

import sqlalchemy as sa

from alembic import op

revision = "0019_g7_model_compatibility_reports"
down_revision = "0018_g7_workspace_asset_authorization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_compatibility_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_artifact_id", sa.String(36), sa.ForeignKey("model_artifacts.id"), nullable=False),
        sa.Column("path_ref", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("byte_size", sa.Integer()),
        sa.Column("header_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("quantization_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("license_status", sa.String(32), nullable=False),
        sa.Column("report_status", sa.String(24), nullable=False),
        sa.Column("blockers_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
    )
    op.create_index("ix_model_compatibility_reports_artifact_created", "model_compatibility_reports", ["model_artifact_id", "created_at"])


def downgrade() -> None:
    raise RuntimeError("Model compatibility reports are release evidence; restore migration preflight backup")

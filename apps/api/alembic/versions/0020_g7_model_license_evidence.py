"""Persist operator-supplied local model license evidence."""

import sqlalchemy as sa

from alembic import op

revision = "0020_g7_model_license_evidence"
down_revision = "0019_g7_model_compatibility_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_license_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("model_artifact_id", sa.String(36), sa.ForeignKey("model_artifacts.id"), nullable=False),
        sa.Column("path_rel", sa.Text(), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("license_name", sa.String(160), nullable=False),
        sa.Column("license_status", sa.String(32), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.UniqueConstraint("project_id", "model_artifact_id", "evidence_sha256", name="uq_model_license_evidence_snapshot"),
    )
    op.create_index(
        "ix_model_license_evidence_project_artifact",
        "model_license_evidence",
        ["project_id", "model_artifact_id", "created_at"],
    )


def downgrade() -> None:
    raise RuntimeError("Model license evidence is release evidence; restore migration preflight backup")

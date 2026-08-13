"""Persist server-issued workflow validation attestations."""

import sqlalchemy as sa

from alembic import op

revision = "0014_g6_workflow_attest"
down_revision = "0013_g6_artifact_media_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_validation_attestations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workflow_version_id", sa.String(36), sa.ForeignKey("workflow_versions.id"), nullable=False),
        sa.Column("workflow_content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-system"),
    )
    op.create_index(
        "ix_workflow_validation_attestations_version_created",
        "workflow_validation_attestations",
        ["workflow_version_id", "created_at"],
    )


def downgrade() -> None:
    raise RuntimeError("Workflow validation attestations are release evidence; restore migration preflight backup")

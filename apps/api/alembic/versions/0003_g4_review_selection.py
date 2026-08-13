"""G4 selection, review, machine QC and stale propagation schema.

Revision ID: 0003_g4_review_selection
Revises: 0002_g3_config_media_import
"""

import sqlalchemy as sa

from alembic import op

revision = "0003_g4_review_selection"
down_revision = "0002_g3_config_media_import"
branch_labels = None
depends_on = None


def _audit_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by", sa.Text(), nullable=False, server_default=sa.text("'system'")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default=sa.text("'v2'")),
    )


def upgrade() -> None:
    op.add_column("review_decisions", sa.Column("subject_revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("review_decisions", sa.Column("is_stale", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("review_decisions", sa.Column("stale_reason", sa.Text()))
    op.add_column("selections", sa.Column("source_revision", sa.Integer(), nullable=False, server_default="1"))
    op.create_table(
        "review_annotations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("review_decision_id", sa.String(36), nullable=False),
        sa.Column("media_version_id", sa.String(36), nullable=False),
        sa.Column("time_us", sa.Integer()),
        sa.Column("annotation_type", sa.String(32), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("frame_rel", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["review_decision_id"], ["review_decisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_version_id"], ["media_versions.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "review_batch_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_review_decisions_stale ON review_decisions(is_stale, decision)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_selections_media_version ON selections(media_version_id, selection_type)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_selections_media_version")
    op.execute("DROP INDEX IF EXISTS ix_review_decisions_stale")
    op.drop_table("review_batch_plans")
    op.drop_table("review_annotations")
    op.drop_column("selections", "source_revision")
    op.drop_column("review_decisions", "stale_reason")
    op.drop_column("review_decisions", "is_stale")
    op.drop_column("review_decisions", "subject_revision")

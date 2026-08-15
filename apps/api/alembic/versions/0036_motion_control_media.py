"""Persist immutable motion-mask/vector/keyframe control bindings."""

import sqlalchemy as sa

from alembic import op


revision = "0036_motion_control_media"
down_revision = "0035_declarative_automation_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "motion_controls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_media_version_id", sa.String(36), nullable=False),
        sa.Column("control_media_version_id", sa.String(36), nullable=False),
        sa.Column("profile_version_id", sa.String(36), nullable=False),
        sa.Column("control_kind", sa.String(24), nullable=False),
        sa.Column("operation", sa.String(24), nullable=False),
        sa.Column("subject_role", sa.String(80), nullable=False),
        sa.Column("control_payload_json", sa.Text(), nullable=False),
        sa.Column("control_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["source_media_version_id"], ["media_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["control_media_version_id"], ["media_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["profile_version_id"], ["execution_profile_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("source_media_version_id", "control_hash", name="uq_motion_controls_source_hash"),
    )
    op.create_index("ix_motion_controls_source_created", "motion_controls", ["source_media_version_id", "created_at"])
    op.create_index("ix_motion_controls_profile_kind", "motion_controls", ["profile_version_id", "control_kind", "operation"])


def downgrade() -> None:
    raise RuntimeError("Motion control media is release history; restore migration preflight backup")

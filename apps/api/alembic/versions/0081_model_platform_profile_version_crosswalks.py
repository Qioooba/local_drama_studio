"""Persist operator-approved V1-to-V2 ProfileVersion migration decisions.

Revision ID: 0081_model_platform_profile_version_crosswalks
Revises: 0080_model_platform_snapshot_execution_binding
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0081_model_platform_profile_version_crosswalks"
down_revision = "0080_model_platform_snapshot_execution_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mp_legacy_profile_version_crosswalks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "legacy_execution_profile_version_id",
            sa.String(36),
            sa.ForeignKey("execution_profile_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "v2_execution_profile_version_id",
            sa.String(36),
            sa.ForeignKey("mp_execution_profile_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "capability_definition_id",
            sa.String(36),
            sa.ForeignKey("mp_capability_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("approval_reason", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.String(120), nullable=False),
        sa.Column("approved_at", sa.Text(), nullable=False),
        sa.Column("revoked_by", sa.String(120)),
        sa.Column("revocation_reason", sa.Text()),
        sa.Column("revoked_at", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "legacy_execution_profile_version_id",
            "v2_execution_profile_version_id",
            name="uq_mp_legacy_profile_crosswalk_pair",
        ),
    )
    op.create_index(
        "ix_mp_legacy_profile_crosswalk_legacy_status",
        "mp_legacy_profile_version_crosswalks",
        ["legacy_execution_profile_version_id", "status"],
    )
    op.create_index(
        "ix_mp_legacy_profile_crosswalk_v2_status",
        "mp_legacy_profile_version_crosswalks",
        ["v2_execution_profile_version_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_mp_legacy_profile_crosswalk_v2_status", table_name="mp_legacy_profile_version_crosswalks")
    op.drop_index("ix_mp_legacy_profile_crosswalk_legacy_status", table_name="mp_legacy_profile_version_crosswalks")
    op.drop_table("mp_legacy_profile_version_crosswalks")

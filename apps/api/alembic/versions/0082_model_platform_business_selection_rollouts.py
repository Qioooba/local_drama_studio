"""Persist explicit eligibility gates for V1-to-V2 business selection cutovers.

Revision ID: 0082_model_platform_business_selection_rollouts
Revises: 0081_model_platform_profile_version_crosswalks
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0082_model_platform_business_selection_rollouts"
down_revision = "0081_model_platform_profile_version_crosswalks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mp_business_selection_rollouts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("business_surface", sa.String(80), nullable=False),
        sa.Column(
            "capability_definition_id",
            sa.String(36),
            sa.ForeignKey("mp_capability_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("approval_reason", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.String(120), nullable=False),
        sa.Column("approved_at", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "business_surface",
            "capability_definition_id",
            "scope_type",
            name="uq_mp_business_selection_rollout",
        ),
    )
    op.create_index(
        "ix_mp_business_selection_rollouts_lookup",
        "mp_business_selection_rollouts",
        ["business_surface", "capability_definition_id", "scope_type", "state"],
    )


def downgrade() -> None:
    op.drop_index("ix_mp_business_selection_rollouts_lookup", table_name="mp_business_selection_rollouts")
    op.drop_table("mp_business_selection_rollouts")

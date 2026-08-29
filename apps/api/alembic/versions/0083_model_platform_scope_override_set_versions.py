"""Version ScopeOverrideSets instead of mutating assignment parameter JSON.

Revision ID: 0083_model_platform_scope_override_set_versions
Revises: 0082_model_platform_business_selection_rollouts
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0083_model_platform_scope_override_set_versions"
down_revision = "0082_model_platform_business_selection_rollouts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Prior V2 services rejected scope overrides, so a supported installation
    # should contain only the empty compatibility payload.  Do not silently
    # discard out-of-band/manual JSON: it lacks the Profile binding, approval
    # and version identity required by ScopeOverrideSetVersion.
    connection = op.get_bind()
    legacy_override_count = connection.execute(
        sa.text(
            """SELECT COUNT(*) FROM mp_capability_assignments
            WHERE override_json IS NOT NULL AND TRIM(override_json) NOT IN ('', '{}')"""
        )
    ).scalar_one()
    if int(legacy_override_count) > 0:
        raise RuntimeError(
            "MP_SCOPE_OVERRIDE_MIGRATION_REQUIRED: non-empty mp_capability_assignments.override_json "
            "must be reviewed and converted to a Profile-bound ScopeOverrideSetVersion before upgrade."
        )
    op.create_table(
        "mp_scope_override_set_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.String(36), nullable=False),
        sa.Column("capability_definition_id", sa.String(36), sa.ForeignKey("mp_capability_definitions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("execution_profile_version_id", sa.String(36), sa.ForeignKey("mp_execution_profile_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("values_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("scope_type", "scope_id", "capability_definition_id", "version_no", name="uq_mp_scope_override_set_version"),
    )
    op.create_index(
        "ix_mp_scope_override_set_current_lookup",
        "mp_scope_override_set_versions",
        ["scope_type", "scope_id", "capability_definition_id", "execution_profile_version_id", "version_no"],
    )
    op.add_column("mp_capability_assignments", sa.Column("override_set_version_id", sa.String(36)))


def downgrade() -> None:
    op.drop_column("mp_capability_assignments", "override_set_version_id")
    op.drop_index("ix_mp_scope_override_set_current_lookup", table_name="mp_scope_override_set_versions")
    op.drop_table("mp_scope_override_set_versions")

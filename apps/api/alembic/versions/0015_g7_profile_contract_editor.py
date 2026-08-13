"""G7 immutable profile contract editing and validation evidence.

Revision ID: 0015_g7_profile_contract_editor
Revises: 0014_g6_workflow_attest
"""

import sqlalchemy as sa

from alembic import op

revision = "0015_g7_profile_contract_editor"
down_revision = "0014_g6_workflow_attest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("execution_profile_versions", sa.Column("output_contract_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("execution_profile_versions", sa.Column("resource_policy_json", sa.Text(), nullable=False, server_default="{}"))
    op.create_table(
        "profile_validation_attestations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("profile_version_id", sa.String(36), sa.ForeignKey("execution_profile_versions.id"), nullable=False),
        sa.Column("contract_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("checks_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
    )
    op.create_index(
        "ix_profile_validation_attestations_version_created",
        "profile_validation_attestations",
        ["profile_version_id", "created_at"],
    )


def downgrade() -> None:
    raise RuntimeError("Profile validation attestations are release evidence; restore migration preflight backup")

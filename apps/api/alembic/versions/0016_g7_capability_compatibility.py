"""G7 capability compatibility attestations."""

import sqlalchemy as sa

from alembic import op

revision = "0016_g7_capability_compatibility"
down_revision = "0015_g7_profile_contract_editor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_compatibility_attestations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("profile_version_id", sa.String(36), sa.ForeignKey("execution_profile_versions.id"), nullable=False),
        sa.Column("contract_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("checks_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
    )
    op.create_index(
        "ix_profile_compatibility_attestations_version_created",
        "profile_compatibility_attestations",
        ["profile_version_id", "created_at"],
    )


def downgrade() -> None:
    raise RuntimeError("Capability compatibility attestations are release evidence; restore migration preflight backup")

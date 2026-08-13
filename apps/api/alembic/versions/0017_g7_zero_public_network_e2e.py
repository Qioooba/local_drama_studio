"""Persist G7 zero-public-network full-chain attestations."""

import sqlalchemy as sa

from alembic import op

revision = "0017_g7_zero_public_network_e2e"
down_revision = "0016_g7_capability_compatibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "g7_network_e2e_attestations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("local_requests_json", sa.Text(), nullable=False),
        sa.Column("blocked_public_attempts_json", sa.Text(), nullable=False),
        sa.Column("observed_connections_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
    )
    op.create_index(
        "ix_g7_network_e2e_attestations_project_created",
        "g7_network_e2e_attestations",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    raise RuntimeError("Network egress attestations are release evidence; restore migration preflight backup")

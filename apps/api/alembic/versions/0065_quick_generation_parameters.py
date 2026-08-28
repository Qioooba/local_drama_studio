"""Add per-run model parameters and reusable quick-generation presets.

Revision ID: 0065_quick_generation_parameters
Revises: 0064_quick_generation_domain
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0065_quick_generation_parameters"
down_revision = "0064_quick_generation_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quick_generation_runs",
        sa.Column("model_parameters_json", sa.Text(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "quick_generation_presets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("capability", sa.String(120), nullable=False),
        sa.Column("execution_profile_version_id", sa.String(64), nullable=False),
        sa.Column("parameters_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("favorite", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(
            ["execution_profile_version_id"],
            ["execution_profile_versions.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("capability", "name", name="uq_quick_generation_preset_capability_name"),
    )
    op.create_index(
        "ix_quick_generation_presets_list",
        "quick_generation_presets",
        ["capability", "favorite", "updated_at"],
    )


def downgrade() -> None:
    raise RuntimeError("Quick-generation parameter presets are user data; restore the migration preflight backup")

"""Persist explicit project creation structure and production presentation fields."""

import sqlalchemy as sa

from alembic import op

revision = "0023_project_creation_spec"
down_revision = "0022_project_package_import_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("width", sa.Integer()))
    op.add_column("projects", sa.Column("height", sa.Integer()))
    op.add_column("projects", sa.Column("primary_language", sa.String(32)))
    op.add_column("projects", sa.Column("subtitle_mode", sa.String(24)))
    op.add_column("projects", sa.Column("subtitle_language", sa.String(32)))


def downgrade() -> None:
    raise RuntimeError("Project creation specs require backup restore for downgrade")

"""Persist idempotent project-package import journals and receipts."""

import sqlalchemy as sa

from alembic import op

revision = "0022_project_package_import_receipts"
down_revision = "0021_g10_scale_read_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_package_imports",
        sa.Column("operation_key", sa.String(64), primary_key=True),
        sa.Column("stage_token", sa.String(64), nullable=False),
        sa.Column("identity_mode", sa.String(48), nullable=False),
        sa.Column("source_project_id", sa.String(36), nullable=False),
        sa.Column("target_project_id", sa.String(36), nullable=False),
        sa.Column("target_code", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.CheckConstraint("status IN ('PREPARING','COMPLETED','FAILED')", name="ck_project_package_import_status"),
        sa.UniqueConstraint("stage_token", "identity_mode", "target_code", name="uq_project_package_import_decision"),
    )
    op.create_index("ix_project_package_imports_status_updated", "project_package_imports", ["status", "updated_at"])


def downgrade() -> None:
    raise RuntimeError("Project package receipts are recovery evidence; restore migration preflight backup")

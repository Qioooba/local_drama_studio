"""G6 workflow packages and ComfyUI execution capture fields."""

import sqlalchemy as sa

from alembic import op

revision = "0005_g6_comfy_workflows"
down_revision = "0004_g5_job_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_versions", sa.Column("content_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("workflow_versions", sa.Column("package_rel_path", sa.Text()))
    op.add_column("workflow_versions", sa.Column("node_bindings_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("workflow_versions", sa.Column("runtime_contract_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("workflow_versions", sa.Column("published_at", sa.Text()))
    op.add_column("job_attempts", sa.Column("comfy_prompt_id", sa.String(200)))
    op.add_column("job_attempts", sa.Column("comfy_client_id", sa.String(200)))
    op.add_column("job_attempts", sa.Column("sandbox_rel_path", sa.Text()))
    op.create_index("ix_workflow_versions_status", "workflow_versions", ["status"])


def downgrade() -> None:
    raise RuntimeError("G6 migration is not safely downgradeable on SQLite; restore migration preflight backup")

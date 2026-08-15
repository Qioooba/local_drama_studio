"""Version post-process recipes and freeze enhancement execution evidence.

Revision ID: 0030_versioned_post_process_chain
Revises: 0029_user_supplied_model_policy
"""

import sqlalchemy as sa

from alembic import op

revision = "0030_versioned_post_process_chain"
down_revision = "0029_user_supplied_model_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("post_process_recipes", sa.Column("recipe_key", sa.String(120)))
    op.add_column("post_process_recipes", sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("post_process_recipes", sa.Column("parent_recipe_id", sa.String(36)))
    op.add_column("post_process_recipes", sa.Column("recipe_hash", sa.String(64)))
    op.add_column("post_process_recipes", sa.Column("published_at", sa.Text()))
    op.execute("UPDATE post_process_recipes SET recipe_key=code WHERE recipe_key IS NULL")
    op.create_index("uq_post_process_recipe_key_version", "post_process_recipes", ["recipe_key", "version_no"], unique=True)
    op.create_index("ix_post_process_recipe_key_status", "post_process_recipes", ["recipe_key", "status"])

    op.add_column("enhancement_runs", sa.Column("plan_hash", sa.String(64)))
    op.add_column("enhancement_runs", sa.Column("input_sha256", sa.String(64)))
    op.add_column("enhancement_runs", sa.Column("output_sha256", sa.String(64)))
    op.add_column("enhancement_runs", sa.Column("execution_snapshot_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("enhancement_runs", sa.Column("qc_json", sa.Text(), nullable=False, server_default="{}"))


def downgrade() -> None:
    raise RuntimeError("Post-process recipe and enhancement history are immutable; restore migration preflight backup")

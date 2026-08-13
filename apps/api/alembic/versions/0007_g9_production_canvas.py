"""G9 production canvas layout and execution preflight schema."""

import sqlalchemy as sa

from alembic import op

revision = "0007_g9_production_canvas"
down_revision = "0006_g8_timeline_audio_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "canvas_layouts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_id", sa.String(36), nullable=False),
        sa.Column("layout_json", sa.Text(), nullable=False),
        sa.Column("layout_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v2"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("scope_type", "scope_id", name="uq_canvas_layout_scope"),
    )
    op.create_table(
        "canvas_execution_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_id", sa.String(36), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("from_node_id", sa.Text()),
        sa.Column("to_node_id", sa.Text()),
        sa.Column("node_ids_json", sa.Text(), nullable=False),
        sa.Column("blockers_json", sa.Text(), nullable=False),
        sa.Column("estimate_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v2"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_canvas_execution_scope", "canvas_execution_plans", ["scope_type", "scope_id", "created_at"])


def downgrade() -> None:
    raise RuntimeError("G9 migration is not safely downgradeable on SQLite; restore migration preflight backup")

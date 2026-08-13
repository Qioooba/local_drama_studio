"""G6 immutable prompts and prompt revisions for variant branching."""

import sqlalchemy as sa

from alembic import op

revision = "0008_g6_prompt_revisions"
down_revision = "0007_g9_production_canvas"
branch_labels = None
depends_on = None


def _audit_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v2"),
    )


def upgrade() -> None:
    op.create_table(
        "prompts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("owner_type", sa.String(40), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "prompt_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("prompt_id", sa.String(36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.String(36)),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("structured_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["prompt_id"], ["prompts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_revision_id"], ["prompt_revisions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("prompt_id", "revision_no", name="uq_prompt_revisions_no"),
    )
    op.create_index("ix_prompts_project_owner", "prompts", ["project_id", "owner_type", "owner_id"])
    op.create_index("ix_prompt_revisions_prompt", "prompt_revisions", ["prompt_id", "revision_no"])


def downgrade() -> None:
    raise RuntimeError("Prompt revisions are immutable history; restore migration preflight backup")

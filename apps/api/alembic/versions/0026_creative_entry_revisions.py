"""Persist versioned creative bible and asset text entries."""

import sqlalchemy as sa

from alembic import op

revision = "0026_creative_entry_revisions"
down_revision = "0025_episode_scene_ranges"
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
        "creative_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("current_revision_id", sa.String(36)),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "kind", "code", name="uq_creative_entries_project_kind_code"),
        sa.CheckConstraint("kind IN ('SERIES_BIBLE','CHARACTER','SCENE','PROP','COSTUME','STYLE','VOICE')", name="ck_creative_entries_kind"),
    )
    op.create_table(
        "creative_entry_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entry_id", sa.String(36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.String(36)),
        sa.Column("restored_from_revision_id", sa.String(36)),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["entry_id"], ["creative_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_revision_id"], ["creative_entry_revisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["restored_from_revision_id"], ["creative_entry_revisions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("entry_id", "revision_no", name="uq_creative_entry_revisions_no"),
    )
    op.create_index("ix_creative_entries_project_kind", "creative_entries", ["project_id", "kind", "code"])
    op.create_index("ix_creative_entry_revisions_entry", "creative_entry_revisions", ["entry_id", "revision_no"])


def downgrade() -> None:
    raise RuntimeError("Creative entry revisions are immutable history; restore migration preflight backup")

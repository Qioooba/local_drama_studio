"""Add immutable human revisions for AI breakdown drafts."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0056_breakdown_draft_revisions"
down_revision = "0055_breakdown_scene_applications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "script_breakdown_draft_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("breakdown_draft_id", sa.String(36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.String(36)),
        sa.Column("draft_json", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["breakdown_draft_id"], ["script_breakdown_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_revision_id"], ["script_breakdown_draft_revisions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("breakdown_draft_id", "revision_no", name="uq_breakdown_draft_revision_no"),
    )
    op.create_index(
        "ix_breakdown_draft_revisions_latest",
        "script_breakdown_draft_revisions",
        ["breakdown_draft_id", "revision_no"],
    )
    with op.batch_alter_table("script_breakdown_scene_applications") as batch:
        batch.add_column(sa.Column("breakdown_draft_revision_id", sa.String(36)))
        batch.create_foreign_key(
            "fk_breakdown_scene_application_revision",
            "script_breakdown_draft_revisions",
            ["breakdown_draft_revision_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    raise RuntimeError("Breakdown draft revisions are append-only; restore the pre-migration backup")

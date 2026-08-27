"""Add canonical Post / Audio mix-draft revisions.

Revision ID: 0063_audio_mix_drafts
Revises: 0062_canonical_job_scope_stage
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0063_audio_mix_drafts"
down_revision = "0062_canonical_job_scope_stage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audio_mix_drafts",
        sa.Column("episode_id", sa.String(36), sa.ForeignKey("episodes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="migration"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("schema_version", sa.String(16), nullable=False, server_default="v2"),
        sa.CheckConstraint("status IN ('DRAFT','READY')", name="ck_audio_mix_draft_status"),
        sa.CheckConstraint("revision >= 0", name="ck_audio_mix_draft_revision"),
    )
    op.execute(
        """INSERT INTO audio_mix_drafts (episode_id,status,created_at,updated_at,created_by,revision,schema_version)
        SELECT episode_id,'DRAFT',MIN(created_at),MAX(updated_at),'migration',COUNT(*),'v2'
        FROM audio_bindings GROUP BY episode_id"""
    )


def downgrade() -> None:
    raise RuntimeError("0063 canonical audio mix revisions are irreversible; restore the pre-upgrade backup instead")

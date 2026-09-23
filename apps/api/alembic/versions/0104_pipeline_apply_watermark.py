"""Per-revision apply watermark for the story-plan draft (PR-05).

Revision ID: 0104_pipeline_apply_watermark
Revises: 0103_outbox_claim_token
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0104_pipeline_apply_watermark"
down_revision = "0103_outbox_claim_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Record WHICH draft revision was applied, not merely THAT one was.

    ``apply_state='APPLIED'`` is a run-level boolean: once the first batch of a
    long manuscript was applied, every later batch's draft was refused with
    ``PIPELINE_ALREADY_APPLIED`` even though its episodes had never reached the
    project.  The watermark stores the applied revision hash and the applied
    episode numbers, so a newer draft revision has a computable, replay-safe diff.
    """

    with op.batch_alter_table("pipeline_runs", recreate="always") as batch:
        batch.add_column(sa.Column("applied_revision_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("applied_episode_numbers_json", sa.Text(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("pipeline_runs", recreate="always") as batch:
        batch.drop_column("applied_episode_numbers_json")
        batch.drop_column("applied_revision_hash")

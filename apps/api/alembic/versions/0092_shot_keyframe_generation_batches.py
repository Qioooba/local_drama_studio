"""Recoverable shot first/end keyframe generation batches.

Revision ID: 0092_shot_keyframe_generation_batches
Revises: 0091_asset_image_generation_batches
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0092_shot_keyframe_generation_batches"
down_revision = "0091_asset_image_generation_batches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shot_keyframe_generation_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("episode_id", sa.String(36), sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("frame_strategy", sa.String(24), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("selected_shot_count", sa.Integer(), nullable=False),
        sa.Column("queued_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.String(16), nullable=False, server_default="v1"),
        sa.CheckConstraint("frame_strategy IN ('FIRST_ONLY','FIRST_AND_LAST')", name="ck_shot_keyframe_batch_strategy"),
        sa.CheckConstraint("candidate_count BETWEEN 1 AND 4", name="ck_shot_keyframe_batch_candidate_count"),
        sa.UniqueConstraint("episode_id", "idempotency_key", name="uq_shot_keyframe_batch_idempotency"),
    )
    op.create_index("ix_shot_keyframe_batches_episode_created", "shot_keyframe_generation_batches", ["episode_id", "created_at"])
    op.create_table(
        "shot_keyframe_generation_batch_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("shot_keyframe_generation_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shot_id", sa.String(36), sa.ForeignKey("shots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("shot_revision", sa.Integer(), nullable=False),
        sa.Column("frame_role", sa.String(24), nullable=False),
        sa.Column("candidate_index", sa.Integer(), nullable=False),
        sa.Column("profile_version_id", sa.String(36), sa.ForeignKey("execution_profile_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("prompt_snapshot", sa.Text(), nullable=False),
        sa.Column("intent_id", sa.String(36), sa.ForeignKey("generation_intents.id", ondelete="SET NULL")),
        sa.Column("variant_id", sa.String(36), sa.ForeignKey("generation_variants.id", ondelete="SET NULL")),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("media_version_id", sa.String(36), sa.ForeignKey("media_versions.id", ondelete="SET NULL")),
        sa.Column("error_code", sa.String(120)),
        sa.Column("error_detail_redacted", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.String(16), nullable=False, server_default="v1"),
        sa.CheckConstraint("frame_role IN ('FIRST_FRAME','END_FRAME')", name="ck_shot_keyframe_item_role"),
        sa.CheckConstraint("candidate_index BETWEEN 1 AND 4", name="ck_shot_keyframe_item_candidate_index"),
        sa.CheckConstraint("status IN ('PLANNED','QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')", name="ck_shot_keyframe_item_status"),
        sa.UniqueConstraint("batch_id", "shot_id", "frame_role", "candidate_index", name="uq_shot_keyframe_batch_item"),
    )
    op.create_index("ix_shot_keyframe_items_batch_status", "shot_keyframe_generation_batch_items", ["batch_id", "status"])
    op.create_index("ix_shot_keyframe_items_job", "shot_keyframe_generation_batch_items", ["job_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_shot_keyframe_items_job", table_name="shot_keyframe_generation_batch_items")
    op.drop_index("ix_shot_keyframe_items_batch_status", table_name="shot_keyframe_generation_batch_items")
    op.drop_table("shot_keyframe_generation_batch_items")
    op.drop_index("ix_shot_keyframe_batches_episode_created", table_name="shot_keyframe_generation_batches")
    op.drop_table("shot_keyframe_generation_batches")


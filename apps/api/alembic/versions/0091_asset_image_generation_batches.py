"""Recoverable cross-asset hero image generation batches.

Revision ID: 0091_asset_image_generation_batches
Revises: 0090_pipeline_draft_review
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0091_asset_image_generation_batches"
down_revision = "0090_pipeline_draft_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """INSERT INTO job_stage_definitions(code,title,domain,active)
        VALUES ('ASSET_IMAGE','资产主图生成','ASSET_BIBLE',1)"""
    )
    op.create_table(
        "asset_image_generation_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_kind", sa.String(24), nullable=False),
        sa.Column("capability", sa.String(64), nullable=False),
        sa.Column("profile_version_id", sa.String(36), sa.ForeignKey("execution_profile_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False, server_default="MISSING_ONLY"),
        sa.Column("status", sa.String(32), nullable=False, server_default="QUEUED"),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("selected_count", sa.Integer(), nullable=False),
        sa.Column("queued_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.String(16), nullable=False, server_default="v1"),
        sa.CheckConstraint("asset_kind IN ('CHARACTER','SCENE','PROP','COSTUME')", name="ck_asset_image_batch_kind"),
        sa.CheckConstraint("mode IN ('MISSING_ONLY')", name="ck_asset_image_batch_mode"),
        sa.CheckConstraint(
            "status IN ('QUEUING','QUEUED','RUNNING','PARTIAL_RUNNING','SUCCEEDED','PARTIAL_FAILED','FAILED','CANCELLED')",
            name="ck_asset_image_batch_status",
        ),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_asset_image_batch_idempotency"),
    )
    op.create_index(
        "ix_asset_image_batches_project_kind_created",
        "asset_image_generation_batches",
        ["project_id", "asset_kind", "created_at"],
    )
    op.create_table(
        "asset_image_generation_batch_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("asset_image_generation_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", sa.String(36), sa.ForeignKey("story_assets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("asset_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PLANNED"),
        sa.Column("prompt_snapshot", sa.Text(), nullable=False),
        sa.Column("intent_id", sa.String(36), sa.ForeignKey("generation_intents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("variant_id", sa.String(36), sa.ForeignKey("generation_variants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("media_version_id", sa.String(36), sa.ForeignKey("media_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reference_id", sa.String(36), sa.ForeignKey("story_asset_references.id", ondelete="SET NULL"), nullable=True),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column("error_detail_redacted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.String(16), nullable=False, server_default="v1"),
        sa.CheckConstraint(
            "status IN ('PLANNED','QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED','SUPERSEDED')",
            name="ck_asset_image_batch_item_status",
        ),
        sa.UniqueConstraint("batch_id", "asset_id", name="uq_asset_image_batch_item_asset"),
    )
    op.create_index(
        "ix_asset_image_batch_items_batch_status",
        "asset_image_generation_batch_items",
        ["batch_id", "status"],
    )
    op.create_index(
        "ix_asset_image_batch_items_job",
        "asset_image_generation_batch_items",
        ["job_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_asset_image_batch_items_job", table_name="asset_image_generation_batch_items")
    op.drop_index("ix_asset_image_batch_items_batch_status", table_name="asset_image_generation_batch_items")
    op.drop_table("asset_image_generation_batch_items")
    op.drop_index("ix_asset_image_batches_project_kind_created", table_name="asset_image_generation_batches")
    op.drop_table("asset_image_generation_batches")
    op.execute("DELETE FROM job_stage_definitions WHERE code='ASSET_IMAGE'")

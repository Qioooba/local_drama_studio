"""Add immutable sample metadata and output facts for video upscale previews."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0097_video_upscale_previews"
down_revision = "0096_production_sessions"
branch_labels = None
depends_on = None


def _audit_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by", sa.Text(), nullable=False, server_default=sa.text("'system'")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False),
    )


def upgrade() -> None:
    op.execute(
        """INSERT INTO job_stage_definitions(code,title,domain,active)
        VALUES ('VIDEO_UPSCALE_DELIVERY','超分成片交付','DELIVERY',1)"""
    )
    op.add_column("video_upscale_runs", sa.Column("episode_id", sa.String(36)))
    op.add_column("video_upscale_runs", sa.Column("source_descriptor_json", sa.Text()))
    op.add_column("video_upscale_runs", sa.Column("effective_options_json", sa.Text()))
    op.add_column("video_upscale_runs", sa.Column("sample_start_ms", sa.Integer()))
    op.add_column("video_upscale_runs", sa.Column("sample_duration_ms", sa.Integer()))
    op.add_column("video_upscale_runs", sa.Column("output_rel_path", sa.Text()))
    op.add_column("video_upscale_runs", sa.Column("output_sha256", sa.String(64)))
    op.add_column("video_upscale_runs", sa.Column("output_byte_size", sa.BigInteger()))
    op.execute(
        """UPDATE video_upscale_runs
        SET episode_id=(SELECT item.episode_id FROM video_upscale_batch_items item WHERE item.id=video_upscale_runs.batch_item_id),
            source_descriptor_json=(SELECT item.source_descriptor_json FROM video_upscale_batch_items item WHERE item.id=video_upscale_runs.batch_item_id),
            effective_options_json=(SELECT item.effective_options_json FROM video_upscale_batch_items item WHERE item.id=video_upscale_runs.batch_item_id)
        WHERE batch_item_id IS NOT NULL"""
    )
    op.create_index("ix_video_upscale_runs_episode_purpose", "video_upscale_runs", ["episode_id", "purpose", "created_at"])
    op.create_table(
        "video_upscale_delivery_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_video_upscale_delivery_batches_idempotency"),
    )
    op.create_table(
        "video_upscale_delivery_batch_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("delivery_batch_id", sa.String(36), nullable=False),
        sa.Column("episode_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("delivery_link_id", sa.String(36), nullable=False),
        sa.Column("selected_render_id", sa.String(36), nullable=False),
        sa.Column("target_version_id", sa.String(36), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["delivery_batch_id"], ["video_upscale_delivery_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["delivery_link_id"], ["video_upscale_delivery_links.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["selected_render_id"], ["episode_render_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_version_id"], ["delivery_target_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("delivery_batch_id", "episode_id", "target_version_id", name="uq_video_upscale_delivery_batch_items_episode_target"),
        sa.UniqueConstraint("delivery_batch_id", "ordinal", name="uq_video_upscale_delivery_batch_items_ordinal"),
    )
    op.create_index(
        "ix_video_upscale_delivery_batch_items_link",
        "video_upscale_delivery_batch_items",
        ["delivery_link_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_video_upscale_delivery_batch_items_link", table_name="video_upscale_delivery_batch_items")
    op.drop_table("video_upscale_delivery_batch_items")
    op.drop_table("video_upscale_delivery_batches")
    op.drop_index("ix_video_upscale_runs_episode_purpose", table_name="video_upscale_runs")
    for column in (
        "output_byte_size",
        "output_sha256",
        "output_rel_path",
        "sample_duration_ms",
        "sample_start_ms",
        "effective_options_json",
        "source_descriptor_json",
        "episode_id",
    ):
        op.drop_column("video_upscale_runs", column)
    op.execute("DELETE FROM job_stage_definitions WHERE code='VIDEO_UPSCALE_DELIVERY'")

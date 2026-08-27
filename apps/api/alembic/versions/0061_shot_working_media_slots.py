"""Make Shot Studio working media a shot-scoped single source of truth.

Revision ID: 0061_shot_working_media_slots
Revises: 0060_visual_lab_runtime_foundation
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0061_shot_working_media_slots"
down_revision = "0060_visual_lab_runtime_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shot_working_media_slots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("shot_id", sa.String(36), nullable=False),
        sa.Column("slot_type", sa.String(24), nullable=False),
        sa.Column("media_version_id", sa.String(36), nullable=False),
        sa.Column("adopted_from_selection_id", sa.String(36)),
        sa.Column("adopted_at", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.String(40), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["shot_id"], ["shots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_version_id"], ["media_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["adopted_from_selection_id"], ["selections.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("shot_id", "slot_type", name="uq_shot_working_media_slot"),
        sa.CheckConstraint("slot_type IN ('KEYFRAME','VIDEO')", name="ck_shot_working_media_slot_type"),
    )
    op.create_index(
        "ix_shot_working_media_slots_version",
        "shot_working_media_slots",
        ["media_version_id"],
    )

    # Historical selections were scoped to MediaAsset. Generated candidates usually
    # own distinct assets, so several assets could be selected for one shot at once.
    # Collapse that history once, choosing the latest valid selection per shot slot.
    op.execute(
        """
        WITH resolved AS (
          SELECT se.id AS selection_id,se.media_version_id,se.created_at,se.created_by,
          CASE
            WHEN ma.owner_type='SHOT' THEN ma.owner_id
            WHEN ma.owner_type='GENERATION_VARIANT' AND gi.owner_type='SHOT' THEN gi.owner_id
            ELSE NULL
          END AS shot_id,
          CASE
            WHEN se.selection_type='KEYFRAME' AND ma.media_kind='IMAGE' AND mv.stage='KEYFRAME'
              THEN 'KEYFRAME'
            WHEN se.selection_type='PROXY_WINNER' AND ma.media_kind='VIDEO' AND mv.stage='PROXY'
              THEN 'VIDEO'
            ELSE NULL
          END AS slot_type
          FROM selections se
          JOIN media_versions mv ON mv.id=se.media_version_id
          JOIN media_assets ma ON ma.id=mv.media_asset_id
          LEFT JOIN generation_variants gv
            ON ma.owner_type='GENERATION_VARIANT' AND gv.id=ma.owner_id
          LEFT JOIN generation_intents gi ON gi.id=gv.intent_id
        ), ranked AS (
          SELECT *,ROW_NUMBER() OVER (
            PARTITION BY shot_id,slot_type ORDER BY created_at DESC,selection_id DESC
          ) AS slot_rank
          FROM resolved
          WHERE shot_id IS NOT NULL AND slot_type IS NOT NULL
        )
        INSERT INTO shot_working_media_slots
        (id,shot_id,slot_type,media_version_id,adopted_from_selection_id,adopted_at,
         created_at,updated_at,created_by,revision,schema_version)
        SELECT
          lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-' ||
          lower(hex(randomblob(2))) || '-' || lower(hex(randomblob(2))) || '-' ||
          lower(hex(randomblob(6))),
          shot_id,slot_type,media_version_id,selection_id,created_at,
          created_at,created_at,COALESCE(created_by,'migration'),1,'v1'
        FROM ranked WHERE slot_rank=1
        """
    )


def downgrade() -> None:
    raise RuntimeError("Shot working slots replace ambiguous per-asset selection truth; restore the pre-migration backup")

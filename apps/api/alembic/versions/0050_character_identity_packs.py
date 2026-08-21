"""Character Identity Pack Version and Slots (PR-CUR-007).

Adds the Character Identity Pack foundation (docs/xinjihua/02, 03 PR-CUR-007):
- character_identity_packs: 角色身份包（基础款/服装/年龄/伤势等）
- character_identity_pack_versions: 身份包版本（草稿/待审/已批准/已作废）
- character_identity_pack_slots: 多视角/三视图 slots (FRONT/LEFT/RIGHT/BACK/FACE/EXPRESSION...)
- shot_asset_bindings.identity_pack_version_id: 镜头级身份包版本绑定

全部 additive，不破坏已有表和数据。
"""

import sqlalchemy as sa

from alembic import op

revision = "0050_character_identity_packs"
down_revision = "0049_canonical_capabilities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "character_identity_packs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("story_asset_id", sa.String(36), nullable=False),
        sa.Column("asset_state_id", sa.String(36), nullable=True),
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("current_version_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["story_asset_id"], ["story_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_state_id"], ["story_asset_states.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("story_asset_id", "code", name="uq_character_identity_packs_asset_code"),
    )
    op.create_index("ix_character_identity_packs_asset", "character_identity_packs", ["story_asset_id", "status"])
    op.create_index("ix_character_identity_packs_project", "character_identity_packs", ["project_id"])

    op.create_table(
        "character_identity_pack_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("pack_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("story_asset_id", sa.String(36), nullable=False),
        sa.Column("asset_state_id", sa.String(36), nullable=True),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("slots_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("generator_job_id", sa.String(36), nullable=True),
        sa.Column("approval_metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["pack_id"], ["character_identity_packs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["story_asset_id"], ["story_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_state_id"], ["story_asset_states.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("pack_id", "version_no", name="uq_character_identity_pack_versions_pack_version"),
    )
    op.create_index("ix_character_identity_pack_versions_pack", "character_identity_pack_versions", ["pack_id", "status"])
    op.create_index("ix_character_identity_pack_versions_project", "character_identity_pack_versions", ["project_id"])

    op.create_table(
        "character_identity_pack_slots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("pack_version_id", sa.String(36), nullable=False),
        sa.Column("slot_kind", sa.String(40), nullable=False),
        sa.Column("media_version_id", sa.String(36), nullable=False),
        sa.Column("is_primary", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("generation_profile_version_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["pack_version_id"], ["character_identity_pack_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_version_id"], ["media_versions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("pack_version_id", "slot_kind", name="uq_character_identity_pack_slots_version_kind"),
    )
    op.create_index("ix_character_identity_pack_slots_version", "character_identity_pack_slots", ["pack_version_id"])

    # Shot-level identity pack version override
    op.add_column("shot_asset_bindings", sa.Column("identity_pack_version_id", sa.String(36), nullable=True))


def downgrade() -> None:
    # Keep the downgrade executable for isolated migration rehearsals.  The
    # pack tables are strictly additive, so reversing them does not mutate any
    # pre-0050 fact.  Drop the referencing column/table graph in reverse order.
    op.drop_column("shot_asset_bindings", "identity_pack_version_id")
    op.drop_index("ix_character_identity_pack_slots_version", table_name="character_identity_pack_slots")
    op.drop_table("character_identity_pack_slots")
    op.drop_index("ix_character_identity_pack_versions_project", table_name="character_identity_pack_versions")
    op.drop_index("ix_character_identity_pack_versions_pack", table_name="character_identity_pack_versions")
    op.drop_table("character_identity_pack_versions")
    op.drop_index("ix_character_identity_packs_project", table_name="character_identity_packs")
    op.drop_index("ix_character_identity_packs_asset", table_name="character_identity_packs")
    op.drop_table("character_identity_packs")

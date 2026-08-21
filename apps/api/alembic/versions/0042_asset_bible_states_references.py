"""V2 asset bible: story asset states, multi-references, and state bindings.

Adds the Character/Scene Bible foundation (docs/xinjihua/02 §17):
- story_asset_states: 资产剧情/造型状态（BASE/OUTFIT/AGE/INJURY/TIME_OF_DAY...）
- story_asset_references: 多参考图（HERO/FRONT/LEFT/RIGHT/THREE_VIEW...）
- episode_asset_state_bindings: 本集默认角色状态
- shot_asset_bindings.asset_state_id: 镜头级状态覆盖
- canonical HERO backfill（幂等）

全部 additive，不删除任何既有表/列。
"""

import sqlalchemy as sa

from alembic import op

revision = "0042_asset_bible_states_references"
down_revision = "0041_character_voice_bindings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "story_asset_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("story_asset_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("state_kind", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("state_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["story_asset_id"], ["story_assets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("story_asset_id", "code", name="uq_story_asset_states_asset_code"),
    )
    op.create_index("ix_story_asset_states_asset", "story_asset_states", ["story_asset_id", "status"])
    op.create_index("ix_story_asset_states_project", "story_asset_states", ["project_id"])

    op.create_table(
        "story_asset_references",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("story_asset_id", sa.String(36), nullable=False),
        sa.Column("asset_state_id", sa.String(36), nullable=True),
        sa.Column("media_version_id", sa.String(36), nullable=False),
        sa.Column("reference_kind", sa.String(40), nullable=False),
        sa.Column("label", sa.String(200), nullable=False, server_default=""),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_locked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("yaw_deg", sa.Float(), nullable=True),
        sa.Column("pitch_deg", sa.Float(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["story_asset_id"], ["story_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_state_id"], ["story_asset_states.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_version_id"], ["media_versions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_story_asset_references_asset_kind", "story_asset_references", ["story_asset_id", "asset_state_id", "reference_kind", "status"])
    op.create_index("ix_story_asset_references_media", "story_asset_references", ["media_version_id"])
    op.create_index("ix_story_asset_references_project", "story_asset_references", ["project_id"])

    op.create_table(
        "episode_asset_state_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("episode_id", sa.String(36), nullable=False),
        sa.Column("story_asset_id", sa.String(36), nullable=False),
        sa.Column("asset_state_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["story_asset_id"], ["story_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_state_id"], ["story_asset_states.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("episode_id", "story_asset_id", name="uq_episode_asset_state_binding"),
    )
    op.create_index("ix_episode_asset_state_bindings_episode", "episode_asset_state_bindings", ["episode_id"])
    op.create_index("ix_episode_asset_state_bindings_asset", "episode_asset_state_bindings", ["story_asset_id"])

    # Shot-level asset state override: nullable, additive.
    op.add_column("shot_asset_bindings", sa.Column("asset_state_id", sa.String(36), nullable=True))

    # Canonical HERO backfill: one HERO reference per asset that has a
    # canonical media version, idempotent (skip if HERO already exists).
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """SELECT a.id AS asset_id, a.project_id, a.canonical_media_version_id, a.created_by, a.updated_at
            FROM story_assets a
            WHERE a.canonical_media_version_id IS NOT NULL
            AND a.status = 'ACTIVE'
            AND NOT EXISTS (
                SELECT 1 FROM story_asset_references r
                WHERE r.story_asset_id = a.id AND r.reference_kind = 'HERO'
            )"""
        )
    ).mappings()
    for row in rows:
        reference_id = _new_id()
        connection.execute(
            sa.text(
                """INSERT INTO story_asset_references
                (id, project_id, story_asset_id, asset_state_id, media_version_id, reference_kind,
                 label, priority, is_locked, yaw_deg, pitch_deg, metadata_json, status,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (:id, :project_id, :asset_id, NULL, :media_version_id, 'HERO',
                 '', 100, 1, NULL, NULL, '{}', 'ACTIVE',
                 :now, :now, :created_by, 1, 'v1')"""
            ),
            {
                "id": reference_id,
                "project_id": row["project_id"],
                "asset_id": row["asset_id"],
                "media_version_id": row["canonical_media_version_id"],
                "now": row["updated_at"],
                "created_by": row["created_by"],
            },
        )


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())


def downgrade() -> None:
    raise RuntimeError("V2 asset bible is append-only release history; restore migration preflight backup")

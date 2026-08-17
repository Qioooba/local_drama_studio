"""G11 character-to-voice bindings for multi-voice TTS orchestration."""

import sqlalchemy as sa

from alembic import op

revision = "0041_character_voice_bindings"
down_revision = "0040_story_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "character_voice_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("character_asset_id", sa.String(36), nullable=False),
        sa.Column("voice_profile_version_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v2"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["character_asset_id"], ["story_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["voice_profile_version_id"], ["voice_profile_versions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("character_asset_id", name="uq_character_voice_binding_character"),
    )
    op.create_index("ix_character_voice_bindings_project", "character_voice_bindings", ["project_id"])


def downgrade() -> None:
    raise RuntimeError("G11 character voice bindings are append-only; restore migration preflight backup")

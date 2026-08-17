"""G11 story asset library: characters/scenes/props/costumes with canonical references."""

import sqlalchemy as sa

from alembic import op

revision = "0040_story_assets"
down_revision = "0039_automation_task_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "story_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("canonical_media_version_id", sa.String(36), nullable=True),
        sa.Column("extra_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v2"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "code", name="uq_story_assets_project_code"),
    )
    op.create_index("ix_story_assets_project_kind", "story_assets", ["project_id", "kind"])
    op.create_table(
        "shot_asset_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("shot_id", sa.String(36), nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("role_in_shot", sa.String(120), nullable=False, server_default="main"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v2"),
        sa.ForeignKeyConstraint(["shot_id"], ["shots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["story_assets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("shot_id", "asset_id", "role_in_shot", name="uq_shot_asset_bindings"),
    )
    op.create_index("ix_shot_asset_bindings_asset", "shot_asset_bindings", ["asset_id"])
    op.create_index("ix_shot_asset_bindings_shot", "shot_asset_bindings", ["shot_id"])


def downgrade() -> None:
    raise RuntimeError("G11 story asset library is append-only; restore migration preflight backup")

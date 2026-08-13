"""G8 timeline, audio/subtitle, enhancement and delivery evidence schema."""

import sqlalchemy as sa

from alembic import op

revision = "0006_g8_timeline_audio_delivery"
down_revision = "0005_g6_comfy_workflows"
branch_labels = None
depends_on = None


def _audit_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by", sa.Text(), nullable=False, server_default=sa.text("'system'")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default=sa.text("'v2'")),
    )


def upgrade() -> None:
    op.create_table(
        "subtitle_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("episode_id", sa.String(36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("input_snapshot_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("episode_id", "revision_no", name="uq_subtitle_revisions_no"),
    )
    op.create_table(
        "subtitle_cues",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("subtitle_revision_id", sa.String(36), nullable=False),
        sa.Column("cue_no", sa.Integer(), nullable=False),
        sa.Column("start_us", sa.Integer(), nullable=False),
        sa.Column("end_us", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("style_json", sa.Text(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["subtitle_revision_id"], ["subtitle_revisions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("subtitle_revision_id", "cue_no", name="uq_subtitle_cues_no"),
        sa.CheckConstraint("start_us >= 0", name="ck_subtitle_cues_start"),
        sa.CheckConstraint("end_us > start_us", name="ck_subtitle_cues_range"),
    )
    op.create_table(
        "audio_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("episode_id", sa.String(36), nullable=False),
        sa.Column("media_version_id", sa.String(36), nullable=False),
        sa.Column("track_type", sa.String(32), nullable=False),
        sa.Column("start_us", sa.Integer(), nullable=False),
        sa.Column("end_us", sa.Integer(), nullable=False),
        sa.Column("gain_db", sa.Float(), nullable=False, server_default="0"),
        sa.Column("source_license_status", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False, server_default="{}"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_version_id"], ["media_versions.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("start_us >= 0 AND end_us > start_us", name="ck_audio_bindings_range"),
    )
    op.create_table(
        "post_process_recipes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(120), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("steps_json", sa.Text(), nullable=False),
        sa.Column("capability_contract_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
    )
    op.create_table(
        "enhancement_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("input_media_version_id", sa.String(36), nullable=False),
        sa.Column("recipe_id", sa.String(36), nullable=False),
        sa.Column("output_media_version_id", sa.String(36)),
        sa.Column("parameters_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error_detail", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["input_media_version_id"], ["media_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recipe_id"], ["post_process_recipes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["output_media_version_id"], ["media_versions.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "delivery_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("delivery_package_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("manifest_sha256", sa.String(64)),
        sa.Column("note", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["delivery_package_id"], ["delivery_packages.id"], ondelete="CASCADE"),
    )
    op.add_column("episode_render_versions", sa.Column("duration_ms", sa.Integer()))
    op.add_column("episode_render_versions", sa.Column("mime_type", sa.String(128), server_default="video/mp4"))
    op.create_index("ix_subtitle_cues_revision_range", "subtitle_cues", ["subtitle_revision_id", "start_us", "end_us"])
    op.create_index("ix_audio_bindings_episode_range", "audio_bindings", ["episode_id", "start_us", "end_us"])
    op.create_index("ix_enhancement_runs_status", "enhancement_runs", ["status"])


def downgrade() -> None:
    raise RuntimeError("G8 migration is not safely downgradeable on SQLite; restore migration preflight backup")

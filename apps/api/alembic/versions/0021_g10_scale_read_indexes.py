"""Add read-path indexes required by the G10 production-scale fixture."""

from alembic import op

revision = "0021_g10_scale_read_indexes"
down_revision = "0020_g7_model_license_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_media_assets_owner", "media_assets", ["owner_id", "media_kind"])
    op.create_index("ix_media_versions_asset_version", "media_versions", ["media_asset_id", "version_no"])
    op.create_index("ix_generation_intents_owner", "generation_intents", ["owner_id", "status"])
    op.create_index("ix_jobs_subject_state", "jobs", ["subject_id", "state"])
    op.create_index("ix_selections_asset_type", "selections", ["media_asset_id", "selection_type"])
    op.create_index("ix_review_decisions_subject_created", "review_decisions", ["subject_type", "subject_id", "created_at"])
    op.create_index("ix_timeline_revisions_episode_revision", "timeline_revisions", ["episode_id", "revision_no"])
    op.create_index("ix_subtitle_revisions_episode_revision", "subtitle_revisions", ["episode_id", "revision_no"])
    op.create_index("ix_episode_renders_episode_created", "episode_render_versions", ["episode_id", "created_at"])
    op.create_index("ix_delivery_packages_render_created", "delivery_packages", ["episode_render_version_id", "created_at"])


def downgrade() -> None:
    raise RuntimeError("G10 scale indexes require backup restore for release rollback rehearsal")

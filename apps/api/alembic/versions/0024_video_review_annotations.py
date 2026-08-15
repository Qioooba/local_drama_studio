"""Persist immutable video timecode annotations and rework links."""

import sqlalchemy as sa

from alembic import op

revision = "0024_video_review_annotations"
down_revision = "0023_project_creation_spec"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_review_annotations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("media_version_id", sa.String(36), sa.ForeignKey("media_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("timecode_ms", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("snapshot_media_version_id", sa.String(36), sa.ForeignKey("media_versions.id", ondelete="RESTRICT")),
        sa.Column("rework_job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False, server_default="v2"),
        sa.CheckConstraint("timecode_ms >= 0", name="ck_video_review_annotation_timecode"),
        sa.CheckConstraint(
            "category IN ('IDENTITY','MOTION','ARTIFACT','FLICKER','AUDIO_SYNC','SUBTITLE','CONTINUITY','OTHER')",
            name="ck_video_review_annotation_category",
        ),
    )
    op.create_index(
        "ix_video_review_annotations_media_time",
        "video_review_annotations",
        ["media_version_id", "timecode_ms", "created_at"],
    )


def downgrade() -> None:
    raise RuntimeError("Video review annotations are audit evidence; restore migration preflight backup")

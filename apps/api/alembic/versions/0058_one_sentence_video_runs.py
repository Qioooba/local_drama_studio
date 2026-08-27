"""Durable one-sentence direct-video and keyframe-to-video runs.

Revision ID: 0058_one_sentence_video_runs
Revises: 0057_provider_connections
"""

import sqlalchemy as sa

from alembic import op

revision = "0058_one_sentence_video_runs"
down_revision = "0057_provider_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "one_sentence_video_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("story_json", sa.Text(), nullable=False),
        sa.Column("story_sha256", sa.String(64), nullable=False),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("llm_profile_version_id", sa.String(64), nullable=False),
        sa.Column("image_profile_version_id", sa.String(64)),
        sa.Column("video_profile_version_id", sa.String(64), nullable=False),
        sa.Column("image_candidate_count", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("remote_outbound_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("plan_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("plan_hash", sa.String(64)),
        sa.Column("execution_fingerprint", sa.String(128)),
        sa.Column("project_id", sa.String(64)),
        sa.Column("episode_id", sa.String(64)),
        sa.Column("shot_id", sa.String(64)),
        sa.Column("intent_id", sa.String(64)),
        sa.Column("prompt_revision_id", sa.String(64)),
        sa.Column("image_intent_id", sa.String(64)),
        sa.Column("image_prompt_revision_id", sa.String(64)),
        sa.Column("selected_candidate_id", sa.String(64)),
        sa.Column("selected_image_media_version_id", sa.String(64)),
        sa.Column("keyframe_review_id", sa.String(64)),
        sa.Column("variant_id", sa.String(64)),
        sa.Column("job_id", sa.String(64)),
        sa.Column("media_version_id", sa.String(64)),
        sa.Column("seed", sa.BigInteger()),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.Text()),
        sa.Column("completed_at", sa.Text()),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
        sa.ForeignKeyConstraint(["shot_id"], ["shots.id"]),
        sa.ForeignKeyConstraint(["intent_id"], ["generation_intents.id"]),
        sa.ForeignKeyConstraint(["prompt_revision_id"], ["prompt_revisions.id"]),
        sa.ForeignKeyConstraint(["image_intent_id"], ["generation_intents.id"]),
        sa.ForeignKeyConstraint(["image_prompt_revision_id"], ["prompt_revisions.id"]),
        sa.ForeignKeyConstraint(["variant_id"], ["generation_variants.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["media_version_id"], ["media_versions.id"]),
        sa.ForeignKeyConstraint(["selected_image_media_version_id"], ["media_versions.id"]),
        sa.CheckConstraint("mode IN ('DIRECT_T2V','KEYFRAME_I2V')", name="ck_one_sentence_video_runs_mode"),
        sa.CheckConstraint("image_candidate_count BETWEEN 1 AND 8", name="ck_one_sentence_video_runs_image_count"),
    )
    op.create_index("ix_one_sentence_video_runs_updated", "one_sentence_video_runs", ["updated_at"])
    op.create_index("ix_one_sentence_video_runs_job", "one_sentence_video_runs", ["job_id"])
    op.create_table(
        "one_sentence_video_candidates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("batch_no", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("variant_id", sa.String(64)),
        sa.Column("job_id", sa.String(64)),
        sa.Column("media_version_id", sa.String(64)),
        sa.Column("parent_candidate_id", sa.String(64)),
        sa.Column("error_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["run_id"], ["one_sentence_video_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["generation_variants.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["media_version_id"], ["media_versions.id"]),
        sa.ForeignKeyConstraint(["parent_candidate_id"], ["one_sentence_video_candidates.id"]),
        sa.UniqueConstraint("run_id", "batch_no", "ordinal", name="uq_one_sentence_video_candidate_slot"),
    )
    op.create_index(
        "ix_one_sentence_video_candidates_run",
        "one_sentence_video_candidates",
        ["run_id", "batch_no", "ordinal"],
    )
    op.create_index("ix_one_sentence_video_candidates_job", "one_sentence_video_candidates", ["job_id"])
    op.create_table(
        "one_sentence_video_run_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["one_sentence_video_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_one_sentence_video_run_events_run", "one_sentence_video_run_events", ["run_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_one_sentence_video_run_events_run", table_name="one_sentence_video_run_events")
    op.drop_table("one_sentence_video_run_events")
    op.drop_index("ix_one_sentence_video_candidates_job", table_name="one_sentence_video_candidates")
    op.drop_index("ix_one_sentence_video_candidates_run", table_name="one_sentence_video_candidates")
    op.drop_table("one_sentence_video_candidates")
    op.drop_index("ix_one_sentence_video_runs_job", table_name="one_sentence_video_runs")
    op.drop_index("ix_one_sentence_video_runs_updated", table_name="one_sentence_video_runs")
    op.drop_table("one_sentence_video_runs")

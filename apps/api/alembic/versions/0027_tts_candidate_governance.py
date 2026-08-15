"""Persist traceable dialogue text, voice authorization and TTS candidates."""

import sqlalchemy as sa

from alembic import op

revision = "0027_tts_candidate_governance"
down_revision = "0026_creative_entry_revisions"
branch_labels = None
depends_on = None


def _audit_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v2"),
    )


def upgrade() -> None:
    op.create_table(
        "dialogue_lines",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("episode_id", sa.String(36), nullable=False),
        sa.Column("shot_id", sa.String(36)),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("speaker", sa.String(160), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shot_id"], ["shots.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("episode_id", "code", name="uq_dialogue_lines_episode_code"),
    )
    op.create_table(
        "dialogue_text_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dialogue_line_id", sa.String(36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("pronunciation_json", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["dialogue_line_id"], ["dialogue_lines.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("dialogue_line_id", "revision_no", name="uq_dialogue_text_revisions_no"),
    )
    op.create_table(
        "voice_profile_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("voice_ref", sa.Text(), nullable=False),
        sa.Column("license_status", sa.String(24), nullable=False),
        sa.Column("license_evidence_json", sa.Text(), nullable=False),
        sa.Column("provider_profile_version_id", sa.String(36)),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_profile_version_id"], ["execution_profile_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "code", "version_no", name="uq_voice_profile_versions_no"),
    )
    op.create_table(
        "tts_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dialogue_text_revision_id", sa.String(36), nullable=False),
        sa.Column("voice_profile_version_id", sa.String(36), nullable=False),
        sa.Column("media_version_id", sa.String(36), nullable=False),
        sa.Column("emotion", sa.String(80), nullable=False),
        sa.Column("speech_rate", sa.Float(), nullable=False),
        sa.Column("seed", sa.BigInteger()),
        sa.Column("model_ref", sa.Text(), nullable=False),
        sa.Column("candidate_kind", sa.String(16), nullable=False),
        sa.Column("provenance_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["dialogue_text_revision_id"], ["dialogue_text_revisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["voice_profile_version_id"], ["voice_profile_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["media_version_id"], ["media_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("dialogue_text_revision_id", "media_version_id", name="uq_tts_candidates_text_media"),
        sa.CheckConstraint("speech_rate >= 0.5 AND speech_rate <= 2.0", name="ck_tts_candidates_speech_rate"),
        sa.CheckConstraint("candidate_kind IN ('PREVIEW','FORMAL')", name="ck_tts_candidates_kind"),
    )
    op.create_table(
        "dialogue_candidate_selections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dialogue_line_id", sa.String(36), nullable=False),
        sa.Column("tts_candidate_id", sa.String(36), nullable=False),
        sa.Column("source_text_revision_id", sa.String(36), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["dialogue_line_id"], ["dialogue_lines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tts_candidate_id"], ["tts_candidates.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_text_revision_id"], ["dialogue_text_revisions.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_dialogue_lines_episode", "dialogue_lines", ["episode_id", "code"])
    op.create_index("ix_tts_candidates_text", "tts_candidates", ["dialogue_text_revision_id", "created_at"])
    op.create_index("ix_dialogue_candidate_selections_line", "dialogue_candidate_selections", ["dialogue_line_id", "created_at"])


def downgrade() -> None:
    raise RuntimeError("TTS candidate history is immutable; restore migration preflight backup")

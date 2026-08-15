"""Require traceable license and loop/fade policy for new audio bindings."""

import sqlalchemy as sa

from alembic import op

revision = "0028_audio_binding_authority"
down_revision = "0027_tts_candidate_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audio_bindings", sa.Column("loop_enabled", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("audio_bindings", sa.Column("fade_in_us", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("audio_bindings", sa.Column("fade_out_us", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("audio_bindings", sa.Column("license_evidence_json", sa.Text(), nullable=False, server_default="{}"))


def downgrade() -> None:
    raise RuntimeError("Audio authorization history is immutable; restore migration preflight backup")

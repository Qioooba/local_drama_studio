"""Canonical capability backfill and legacy alias migration."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0049_canonical_capabilities"
down_revision = "0048_asset_proposals"
branch_labels = None
depends_on = None

CAPABILITY_MIGRATIONS = [
    ("SCRIPT_BREAKDOWN_LLM", "LLM_STORY_PARSE"),
    ("STORY_PARSE", "LLM_STORY_PARSE"),
    ("STORY_BREAKDOWN", "LLM_STORY_PARSE"),
    ("EPISODE_PLAN", "LLM_EPISODE_PLAN"),
    ("PROMPT_REWRITE", "LLM_PROMPT_REWRITE"),
    ("I2V", "VIDEO_I2V"),
    ("IMAGE_TO_VIDEO", "VIDEO_I2V"),
    ("IMAGE2VIDEO", "VIDEO_I2V"),
    ("T2V", "VIDEO_T2V"),
    ("TEXT_TO_VIDEO", "VIDEO_T2V"),
    ("TEXT2VIDEO", "VIDEO_T2V"),
    ("FIRST_FRAME", "VIDEO_FIRST_FRAME"),
    ("FIRST_LAST_FRAME", "VIDEO_FIRST_LAST_FRAME"),
    ("VIDEO_FIRST_LAST", "VIDEO_FIRST_LAST_FRAME"),
    ("MOTION_CONTROL", "VIDEO_MOTION_CONTROL"),
    ("MOTION_BRUSH", "VIDEO_MOTION_CONTROL"),
    ("AUDIO_TTS", "TTS"),
    ("TEXT_TO_SPEECH", "TTS"),
    ("AUDIO_CLONE", "VOICE_CLONE"),
    ("AUDIO_VOICE_CLONE", "VOICE_CLONE"),
    ("LIP_SYNC", "LIPSYNC"),
    ("SFX", "AUDIO_SFX"),
    ("MUSIC", "AUDIO_MUSIC"),
    ("BGM", "AUDIO_MUSIC"),
    ("BGM_GEN", "AUDIO_MUSIC"),
    ("AUDIO_BGM", "AUDIO_MUSIC"),
    ("CHARACTER", "IMAGE_CHARACTER"),
    ("SCENE", "IMAGE_SCENE"),
    ("CONCEPT", "IMAGE_CONCEPT"),
    ("MULTI_VIEW", "IMAGE_MULTI_VIEW"),
    ("EXPRESSION", "IMAGE_EXPRESSION"),
    ("EDIT", "IMAGE_EDIT"),
    ("UPSCALE", "UPSCALE_IMAGE"),
    ("SR_IMAGE", "UPSCALE_IMAGE"),
    ("SR_VIDEO", "UPSCALE_VIDEO"),
]


def upgrade() -> None:
    conn = op.get_bind()

    for old_cap, new_cap in CAPABILITY_MIGRATIONS:
        conn.execute(
            sa.text("UPDATE execution_profile_versions SET capability = :new_cap WHERE UPPER(capability) = :old_cap"),
            {"new_cap": new_cap, "old_cap": old_cap},
        )
        conn.execute(
            sa.text("UPDATE generation_preference_sets SET capability = :new_cap WHERE UPPER(capability) = :old_cap"),
            {"new_cap": new_cap, "old_cap": old_cap},
        )


def downgrade() -> None:
    raise RuntimeError("Canonical capability normalization is one-way and forward-only; restore DB snapshot if needed")

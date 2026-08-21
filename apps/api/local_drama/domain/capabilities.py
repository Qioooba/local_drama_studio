"""Canonical Generation Capabilities and Alias Normalization."""

from __future__ import annotations

from enum import Enum
from typing import Final


class GenerationCapability(str, Enum):
    # LLM
    LLM_STORY_PARSE = "LLM_STORY_PARSE"
    LLM_EPISODE_PLAN = "LLM_EPISODE_PLAN"
    LLM_STORYBOARD = "LLM_STORYBOARD"
    LLM_PROMPT_REWRITE = "LLM_PROMPT_REWRITE"

    # Image
    IMAGE_CONCEPT = "IMAGE_CONCEPT"
    IMAGE_CHARACTER = "IMAGE_CHARACTER"
    IMAGE_SCENE = "IMAGE_SCENE"
    IMAGE_EDIT = "IMAGE_EDIT"
    IMAGE_MULTI_VIEW = "IMAGE_MULTI_VIEW"
    IMAGE_EXPRESSION = "IMAGE_EXPRESSION"

    # Video
    VIDEO_T2V = "VIDEO_T2V"
    VIDEO_I2V = "VIDEO_I2V"
    VIDEO_FIRST_FRAME = "VIDEO_FIRST_FRAME"
    VIDEO_FIRST_LAST_FRAME = "VIDEO_FIRST_LAST_FRAME"
    VIDEO_REFERENCE = "VIDEO_REFERENCE"
    VIDEO_MOTION_CONTROL = "VIDEO_MOTION_CONTROL"

    # Audio
    TTS = "TTS"
    VOICE_CLONE = "VOICE_CLONE"
    LIPSYNC = "LIPSYNC"
    AUDIO_SFX = "AUDIO_SFX"
    AUDIO_MUSIC = "AUDIO_MUSIC"

    # Frame & Post Process
    FRAME_EXTRACT = "FRAME_EXTRACT"
    UPSCALE_IMAGE = "UPSCALE_IMAGE"
    UPSCALE_VIDEO = "UPSCALE_VIDEO"
    POST_PROCESS = "POST_PROCESS"

    # QC
    QC_VISUAL = "QC_VISUAL"
    QC_FACE = "QC_FACE"
    QC_IDENTITY = "QC_IDENTITY"
    QC_CONTINUITY = "QC_CONTINUITY"
    QC_AUDIO = "QC_AUDIO"


CANONICAL_CAPABILITIES: Final[frozenset[str]] = frozenset(c.value for c in GenerationCapability)
VIDEO_GENERATION_CAPABILITIES: Final[tuple[str, ...]] = tuple(
    capability.value
    for capability in GenerationCapability
    if capability.value.startswith("VIDEO_")
)

# Explicit, non-fuzzy alias mapping for historical and legacy names
CAPABILITY_ALIASES: Final[dict[str, str]] = {
    # Script / Story Breakdown
    "SCRIPT_BREAKDOWN_LLM": "LLM_STORY_PARSE",
    "STORY_PARSE": "LLM_STORY_PARSE",
    "STORY_BREAKDOWN": "LLM_STORY_PARSE",
    "EPISODE_PLAN": "LLM_EPISODE_PLAN",
    "PROMPT_REWRITE": "LLM_PROMPT_REWRITE",

    # Video
    "I2V": "VIDEO_I2V",
    "I2V_VIDEO": "VIDEO_I2V",
    "IMAGE_TO_VIDEO": "VIDEO_I2V",
    "IMAGE2VIDEO": "VIDEO_I2V",
    "T2V": "VIDEO_T2V",
    "T2V_VIDEO": "VIDEO_T2V",
    "TEXT_TO_VIDEO": "VIDEO_T2V",
    "TEXT2VIDEO": "VIDEO_T2V",
    "FIRST_FRAME": "VIDEO_FIRST_FRAME",
    "FIRST_LAST_FRAME": "VIDEO_FIRST_LAST_FRAME",
    "VIDEO_FIRST_LAST": "VIDEO_FIRST_LAST_FRAME",
    # The H3 manifest exposes both image-reference (R2V) and video-reference
    # (V2V) routes under the product's single VIDEO_REFERENCE capability.
    "R2V": "VIDEO_REFERENCE",
    "REF2V": "VIDEO_REFERENCE",
    "REFERENCE_TO_VIDEO": "VIDEO_REFERENCE",
    "V2V": "VIDEO_REFERENCE",
    "VIDEO_TO_VIDEO": "VIDEO_REFERENCE",
    "MOTION_CONTROL": "VIDEO_MOTION_CONTROL",
    "MOTION_BRUSH": "VIDEO_MOTION_CONTROL",

    # Audio
    "AUDIO_TTS": "TTS",
    "TEXT_TO_SPEECH": "TTS",
    "AUDIO_CLONE": "VOICE_CLONE",
    "AUDIO_VOICE_CLONE": "VOICE_CLONE",
    "LIP_SYNC": "LIPSYNC",
    "SFX": "AUDIO_SFX",
    "MUSIC": "AUDIO_MUSIC",
    "BGM": "AUDIO_MUSIC",
    "BGM_GEN": "AUDIO_MUSIC",
    "AUDIO_BGM": "AUDIO_MUSIC",

    # Image
    "CHARACTER": "IMAGE_CHARACTER",
    "SCENE": "IMAGE_SCENE",
    "CONCEPT": "IMAGE_CONCEPT",
    "MULTI_VIEW": "IMAGE_MULTI_VIEW",
    "EXPRESSION": "IMAGE_EXPRESSION",
    "EDIT": "IMAGE_EDIT",

    # Upscale / Post
    "UPSCALE": "UPSCALE_IMAGE",
    "SR_IMAGE": "UPSCALE_IMAGE",
    "SR_VIDEO": "UPSCALE_VIDEO",
}


def normalize_capability(name: str) -> str:
    """Normalize capability name to canonical value.
    
    Raises ValueError if name cannot be unambiguously mapped.
    Ambiguous values like generic 'IMAGE_GENERATION' without context are rejected to fail closed.
    """
    cleaned = (name or "").strip().upper()
    if cleaned in CANONICAL_CAPABILITIES:
        return cleaned
    if cleaned in CAPABILITY_ALIASES:
        return CAPABILITY_ALIASES[cleaned]
    raise ValueError(f"Unknown or ambiguous capability: '{name}'. Cannot normalize to canonical capability.")


def is_canonical_capability(name: str) -> bool:
    return (name or "").strip().upper() in CANONICAL_CAPABILITIES

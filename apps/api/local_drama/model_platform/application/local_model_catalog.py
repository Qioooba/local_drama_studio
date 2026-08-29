"""Declared business capabilities for the controlled local model catalog.

The catalog maps release codes from the controlled model-lock manifest, rather
than guessing semantics from file names.  Discovery still only creates
candidates; validation and Profile publication decide executability.
"""

from __future__ import annotations

from types import MappingProxyType

from local_drama.model_platform.domain.capabilities import GenerationCapability

DECLARED_MODEL_CAPABILITIES = MappingProxyType(
    {
        "qwen-image-2512-q5-k-m": (
            GenerationCapability.IMAGE_CONCEPT.value,
            GenerationCapability.IMAGE_CHARACTER.value,
            GenerationCapability.IMAGE_SCENE.value,
        ),
        "qwen-image-edit-2511-q5-k-m": (
            GenerationCapability.IMAGE_EDIT.value,
            GenerationCapability.IMAGE_EXPRESSION.value,
        ),
        "qwen-image-edit-2511-multiple-angles-lora": (GenerationCapability.IMAGE_MULTI_VIEW.value,),
        "minimax-h3-fl2va": (GenerationCapability.VIDEO_FIRST_FRAME.value, GenerationCapability.VIDEO_I2V.value),
        "minimax-h3-ref2va": (GenerationCapability.VIDEO_REFERENCE.value,),
        "ace-step-1.5-xl-sft": (GenerationCapability.AUDIO_MUSIC.value,),
        "qwen3-embedding-8b": (GenerationCapability.EMBEDDING_TEXT.value,),
        "voxcpm2": (GenerationCapability.TTS.value, GenerationCapability.VOICE_CLONE.value),
        "qwen3-asr-1.7b-hf": (GenerationCapability.ASR.value,),
        "qwen3-forced-aligner-0.6b-hf": (GenerationCapability.AUDIO_ALIGNMENT.value,),
        "latentsync-1.6": (GenerationCapability.LIPSYNC.value,),
    }
)

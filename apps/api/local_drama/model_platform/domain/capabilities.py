"""The single capability vocabulary for Model Platform V2.

Capabilities describe a business operation.  They are deliberately distinct
from job types (for example ``EMBEDDING_INDEX``) and from model component
roles (for example ``VAE`` or ``LORA``).  This lets a model be offered through
different runtimes without teaching every business page about a file path,
Ollama tag, Comfy node, or Python environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final, Mapping


class CapabilityFamily(str, Enum):
    TEXT = "TEXT"
    RETRIEVAL = "RETRIEVAL"
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    POST = "POST"
    QUALITY = "QUALITY"


class GenerationCapability(str, Enum):
    # Text and planning
    LLM_STORY_PARSE = "LLM_STORY_PARSE"
    LLM_EPISODE_PLAN = "LLM_EPISODE_PLAN"
    LLM_STORYBOARD = "LLM_STORYBOARD"
    LLM_PROMPT_REWRITE = "LLM_PROMPT_REWRITE"

    # Retrieval is intentionally a first-class capability.  It is normally
    # resolved by index policy rather than presented in a creator picker.
    EMBEDDING_TEXT = "EMBEDDING_TEXT"
    EMBEDDING_MULTIMODAL = "EMBEDDING_MULTIMODAL"
    RERANK = "RERANK"

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
    ASR = "ASR"
    AUDIO_ALIGNMENT = "AUDIO_ALIGNMENT"

    # Frame and post process
    FRAME_EXTRACT = "FRAME_EXTRACT"
    UPSCALE_IMAGE = "UPSCALE_IMAGE"
    UPSCALE_VIDEO = "UPSCALE_VIDEO"
    POST_PROCESS = "POST_PROCESS"

    # Quality control
    QC_VISUAL = "QC_VISUAL"
    QC_FACE = "QC_FACE"
    QC_IDENTITY = "QC_IDENTITY"
    QC_CONTINUITY = "QC_CONTINUITY"
    QC_AUDIO = "QC_AUDIO"


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    """Product metadata consumed by catalogs, resolvers, and page menus."""

    code: str
    title: str
    family: CapabilityFamily
    input_modalities: tuple[str, ...]
    output_modalities: tuple[str, ...]
    business_surfaces: tuple[str, ...]
    background_only: bool = False


def _definition(
    code: GenerationCapability,
    title: str,
    family: CapabilityFamily,
    inputs: tuple[str, ...],
    outputs: tuple[str, ...],
    surfaces: tuple[str, ...],
    *,
    background_only: bool = False,
) -> CapabilityDefinition:
    return CapabilityDefinition(code.value, title, family, inputs, outputs, surfaces, background_only)


_DEFINITIONS: Final[tuple[CapabilityDefinition, ...]] = (
    _definition(GenerationCapability.LLM_STORY_PARSE, "故事拆解", CapabilityFamily.TEXT, ("TEXT",), ("TEXT",), ("quick-create", "story")),
    _definition(GenerationCapability.LLM_EPISODE_PLAN, "分集规划", CapabilityFamily.TEXT, ("TEXT",), ("TEXT",), ("episode-plan",)),
    _definition(GenerationCapability.LLM_STORYBOARD, "分镜规划", CapabilityFamily.TEXT, ("TEXT", "IMAGE"), ("TEXT",), ("episode-plan", "shot-studio")),
    _definition(GenerationCapability.LLM_PROMPT_REWRITE, "提示词优化", CapabilityFamily.TEXT, ("TEXT",), ("TEXT",), ("quick-create", "story", "assets", "shot-studio")),
    _definition(GenerationCapability.EMBEDDING_TEXT, "文本向量化", CapabilityFamily.RETRIEVAL, ("TEXT",), ("VECTOR",), ("project-knowledge",), background_only=True),
    _definition(GenerationCapability.EMBEDDING_MULTIMODAL, "多模态向量化", CapabilityFamily.RETRIEVAL, ("TEXT", "IMAGE"), ("VECTOR",), ("project-knowledge",), background_only=True),
    _definition(GenerationCapability.RERANK, "检索重排", CapabilityFamily.RETRIEVAL, ("TEXT", "TEXT"), ("TEXT",), ("project-knowledge",), background_only=True),
    _definition(GenerationCapability.IMAGE_CONCEPT, "概念图", CapabilityFamily.IMAGE, ("TEXT",), ("IMAGE",), ("quick-create", "assets", "shot-studio")),
    _definition(GenerationCapability.IMAGE_CHARACTER, "角色图", CapabilityFamily.IMAGE, ("TEXT",), ("IMAGE",), ("assets", "shot-studio")),
    _definition(GenerationCapability.IMAGE_SCENE, "场景图", CapabilityFamily.IMAGE, ("TEXT",), ("IMAGE",), ("assets", "shot-studio")),
    _definition(GenerationCapability.IMAGE_EDIT, "图像编辑", CapabilityFamily.IMAGE, ("TEXT", "IMAGE"), ("IMAGE",), ("assets", "shot-studio")),
    _definition(GenerationCapability.IMAGE_MULTI_VIEW, "角色多视图", CapabilityFamily.IMAGE, ("TEXT", "IMAGE"), ("IMAGE",), ("assets",)),
    _definition(GenerationCapability.IMAGE_EXPRESSION, "角色表情", CapabilityFamily.IMAGE, ("TEXT", "IMAGE"), ("IMAGE",), ("assets",)),
    _definition(GenerationCapability.VIDEO_T2V, "文生视频", CapabilityFamily.VIDEO, ("TEXT",), ("VIDEO",), ("quick-create", "shot-studio")),
    _definition(GenerationCapability.VIDEO_I2V, "图生视频", CapabilityFamily.VIDEO, ("TEXT", "IMAGE"), ("VIDEO",), ("quick-create", "shot-studio")),
    _definition(GenerationCapability.VIDEO_FIRST_FRAME, "首帧视频", CapabilityFamily.VIDEO, ("TEXT", "IMAGE"), ("VIDEO",), ("shot-studio",)),
    _definition(GenerationCapability.VIDEO_FIRST_LAST_FRAME, "首尾帧视频", CapabilityFamily.VIDEO, ("TEXT", "IMAGE"), ("VIDEO",), ("shot-studio",)),
    _definition(GenerationCapability.VIDEO_REFERENCE, "参考驱动视频", CapabilityFamily.VIDEO, ("TEXT", "IMAGE", "VIDEO"), ("VIDEO",), ("shot-studio",)),
    _definition(GenerationCapability.VIDEO_MOTION_CONTROL, "运动控制视频", CapabilityFamily.VIDEO, ("TEXT", "IMAGE", "VIDEO"), ("VIDEO",), ("shot-studio",)),
    _definition(GenerationCapability.TTS, "文本转语音", CapabilityFamily.AUDIO, ("TEXT",), ("AUDIO",), ("post-audio", "shot-studio")),
    _definition(GenerationCapability.VOICE_CLONE, "音色克隆", CapabilityFamily.AUDIO, ("TEXT", "AUDIO"), ("AUDIO",), ("assets", "post-audio")),
    _definition(GenerationCapability.LIPSYNC, "唇形同步", CapabilityFamily.POST, ("VIDEO", "AUDIO"), ("VIDEO",), ("post-edit",)),
    _definition(GenerationCapability.AUDIO_SFX, "音效生成", CapabilityFamily.AUDIO, ("TEXT",), ("AUDIO",), ("post-audio",)),
    _definition(GenerationCapability.AUDIO_MUSIC, "音乐生成", CapabilityFamily.AUDIO, ("TEXT",), ("AUDIO",), ("post-audio",)),
    _definition(GenerationCapability.ASR, "语音识别", CapabilityFamily.AUDIO, ("AUDIO",), ("TEXT",), ("post-audio", "post-edit")),
    _definition(GenerationCapability.AUDIO_ALIGNMENT, "字幕强制对齐", CapabilityFamily.AUDIO, ("AUDIO", "TEXT"), ("TEXT",), ("post-audio", "post-edit")),
    _definition(GenerationCapability.FRAME_EXTRACT, "抽帧", CapabilityFamily.POST, ("VIDEO",), ("IMAGE",), ("post-edit",)),
    _definition(GenerationCapability.UPSCALE_IMAGE, "图像超分", CapabilityFamily.POST, ("IMAGE",), ("IMAGE",), ("post-edit",)),
    _definition(GenerationCapability.UPSCALE_VIDEO, "视频超分", CapabilityFamily.POST, ("VIDEO",), ("VIDEO",), ("post-edit",)),
    _definition(GenerationCapability.POST_PROCESS, "媒体后处理", CapabilityFamily.POST, ("IMAGE", "VIDEO", "AUDIO"), ("IMAGE", "VIDEO", "AUDIO"), ("post-edit", "delivery")),
    _definition(GenerationCapability.QC_VISUAL, "视觉质检", CapabilityFamily.QUALITY, ("IMAGE", "VIDEO"), ("TEXT",), ("post-review",)),
    _definition(GenerationCapability.QC_FACE, "人脸质检", CapabilityFamily.QUALITY, ("IMAGE", "VIDEO"), ("TEXT",), ("post-review",)),
    _definition(GenerationCapability.QC_IDENTITY, "身份一致性质检", CapabilityFamily.QUALITY, ("IMAGE", "VIDEO"), ("TEXT",), ("post-review",)),
    _definition(GenerationCapability.QC_CONTINUITY, "连续性质检", CapabilityFamily.QUALITY, ("IMAGE", "VIDEO"), ("TEXT",), ("post-review",)),
    _definition(GenerationCapability.QC_AUDIO, "音频质检", CapabilityFamily.QUALITY, ("AUDIO",), ("TEXT",), ("post-review",)),
)

CAPABILITY_DEFINITIONS: Final[Mapping[str, CapabilityDefinition]] = MappingProxyType({item.code: item for item in _DEFINITIONS})
CANONICAL_CAPABILITIES: Final[frozenset[str]] = frozenset(CAPABILITY_DEFINITIONS)
VIDEO_GENERATION_CAPABILITIES: Final[tuple[str, ...]] = tuple(item.code for item in _DEFINITIONS if item.family is CapabilityFamily.VIDEO)


# Explicit, non-fuzzy aliases retained for historic imports and persisted data.
CAPABILITY_ALIASES: Final[dict[str, str]] = {
    "SCRIPT_BREAKDOWN_LLM": "LLM_STORY_PARSE",
    "STORY_PARSE": "LLM_STORY_PARSE",
    "STORY_BREAKDOWN": "LLM_STORY_PARSE",
    "EPISODE_PLAN": "LLM_EPISODE_PLAN",
    "PROMPT_REWRITE": "LLM_PROMPT_REWRITE",
    "TEXT_EMBEDDING": "EMBEDDING_TEXT",
    "EMBEDDING": "EMBEDDING_TEXT",
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
    "R2V": "VIDEO_REFERENCE",
    "REF2V": "VIDEO_REFERENCE",
    "REFERENCE_TO_VIDEO": "VIDEO_REFERENCE",
    "V2V": "VIDEO_REFERENCE",
    "VIDEO_TO_VIDEO": "VIDEO_REFERENCE",
    "MOTION_CONTROL": "VIDEO_MOTION_CONTROL",
    "MOTION_BRUSH": "VIDEO_MOTION_CONTROL",
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
    "SPEECH_TO_TEXT": "ASR",
    "STT": "ASR",
    "AUDIO_ASR": "ASR",
    "FORCED_ALIGNMENT": "AUDIO_ALIGNMENT",
    "AUDIO_FORCED_ALIGNMENT": "AUDIO_ALIGNMENT",
    "CHARACTER": "IMAGE_CHARACTER",
    "SCENE": "IMAGE_SCENE",
    "CONCEPT": "IMAGE_CONCEPT",
    "MULTI_VIEW": "IMAGE_MULTI_VIEW",
    "EXPRESSION": "IMAGE_EXPRESSION",
    "EDIT": "IMAGE_EDIT",
    "UPSCALE": "UPSCALE_IMAGE",
    "SR_IMAGE": "UPSCALE_IMAGE",
    "SR_VIDEO": "UPSCALE_VIDEO",
}


def normalize_capability(name: str) -> str:
    """Return a canonical capability or fail closed for ambiguous input."""

    cleaned = (name or "").strip().upper()
    if cleaned in CANONICAL_CAPABILITIES:
        return cleaned
    if cleaned in CAPABILITY_ALIASES:
        return CAPABILITY_ALIASES[cleaned]
    raise ValueError(f"Unknown or ambiguous capability: '{name}'. Cannot normalize to canonical capability.")


def capability_definition(name: str) -> CapabilityDefinition:
    """Resolve one user, manifest, or persisted capability value to metadata."""

    return CAPABILITY_DEFINITIONS[normalize_capability(name)]


def is_background_capability(name: str) -> bool:
    return capability_definition(name).background_only


def is_canonical_capability(name: str) -> bool:
    return (name or "").strip().upper() in CANONICAL_CAPABILITIES

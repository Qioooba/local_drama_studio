"""Backward-compatible exports for the V2 model-platform capability catalog.

New code imports from :mod:`local_drama.model_platform.domain.capabilities`.
The re-export keeps the pre-V2 call sites stable during the one-time migration;
it must not grow new behaviour of its own.
"""

from local_drama.model_platform.domain.capabilities import (
    CANONICAL_CAPABILITIES,
    CAPABILITY_ALIASES,
    CAPABILITY_DEFINITIONS,
    VIDEO_GENERATION_CAPABILITIES,
    CapabilityDefinition,
    CapabilityFamily,
    GenerationCapability,
    capability_definition,
    is_background_capability,
    is_canonical_capability,
    normalize_capability,
)

__all__ = [
    "CANONICAL_CAPABILITIES",
    "CAPABILITY_ALIASES",
    "CAPABILITY_DEFINITIONS",
    "VIDEO_GENERATION_CAPABILITIES",
    "CapabilityDefinition",
    "CapabilityFamily",
    "GenerationCapability",
    "capability_definition",
    "is_background_capability",
    "is_canonical_capability",
    "normalize_capability",
]

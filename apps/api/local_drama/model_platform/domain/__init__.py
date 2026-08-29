"""Pure domain contracts for the model platform."""

from .capabilities import (
    CANONICAL_CAPABILITIES,
    CAPABILITY_ALIASES,
    CAPABILITY_DEFINITIONS,
    CapabilityDefinition,
    CapabilityFamily,
    GenerationCapability,
    capability_definition,
    is_background_capability,
    is_canonical_capability,
    normalize_capability,
)
from .models import (
    ArtifactKind,
    ComponentRole,
    ModelArtifactIdentity,
    ModelArtifactLocation,
    ModelComponent,
    ModelReleaseIdentity,
    RuntimeKind,
    RuntimeModelInstallation,
)
from .states import (
    AvailabilitySnapshot,
    IntegrityStatus,
    PresenceStatus,
    PublicationStatus,
    RuntimeStatus,
    ValidationStatus,
)

__all__ = [
    "AvailabilitySnapshot",
    "ArtifactKind",
    "CANONICAL_CAPABILITIES",
    "CAPABILITY_ALIASES",
    "CAPABILITY_DEFINITIONS",
    "CapabilityDefinition",
    "CapabilityFamily",
    "ComponentRole",
    "GenerationCapability",
    "IntegrityStatus",
    "ModelArtifactIdentity",
    "ModelArtifactLocation",
    "ModelComponent",
    "ModelReleaseIdentity",
    "PresenceStatus",
    "PublicationStatus",
    "RuntimeStatus",
    "RuntimeKind",
    "RuntimeModelInstallation",
    "ValidationStatus",
    "capability_definition",
    "is_background_capability",
    "is_canonical_capability",
    "normalize_capability",
]

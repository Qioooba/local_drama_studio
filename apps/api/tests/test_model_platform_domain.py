from __future__ import annotations

import pytest

from local_drama.domain.capabilities import CANONICAL_CAPABILITIES as legacy_capabilities
from local_drama.model_platform.domain import (
    ArtifactKind,
    AvailabilitySnapshot,
    CapabilityFamily,
    ComponentRole,
    IntegrityStatus,
    ModelArtifactIdentity,
    ModelArtifactLocation,
    ModelComponent,
    PresenceStatus,
    PublicationStatus,
    RuntimeKind,
    RuntimeModelInstallation,
    RuntimeStatus,
    ValidationStatus,
    capability_definition,
    is_background_capability,
    normalize_capability,
)


def test_model_platform_is_the_canonical_capability_source_with_legacy_exports() -> None:
    assert "EMBEDDING_TEXT" in legacy_capabilities
    assert normalize_capability("embedding") == "EMBEDDING_TEXT"
    assert normalize_capability("text_embedding") == "EMBEDDING_TEXT"

    definition = capability_definition("EMBEDDING_TEXT")
    assert definition.family is CapabilityFamily.RETRIEVAL
    assert definition.output_modalities == ("VECTOR",)
    assert definition.background_only is True
    assert is_background_capability("EMBEDDING_TEXT") is True


def test_creator_capabilities_remain_visible_to_their_expected_surfaces() -> None:
    concept = capability_definition("IMAGE_CONCEPT")
    assert concept.background_only is False
    assert {"quick-create", "assets", "shot-studio"} <= set(concept.business_surfaces)


def test_availability_never_confuses_a_candidate_pytorch_installation_with_an_executable_route() -> None:
    pending = AvailabilitySnapshot(
        presence=PresenceStatus.PRESENT,
        integrity=IntegrityStatus.VERIFIED,
        runtime=RuntimeStatus.READY,
        validation=ValidationStatus.SMOKE_PASSED,
        publication=PublicationStatus.CANDIDATE,
        route_available=False,
    )

    assert pending.executable is False
    assert pending.blockers == ("publication:CANDIDATE", "route:UNAVAILABLE")


def test_availability_requires_all_independent_lifecycle_states() -> None:
    ready = AvailabilitySnapshot(
        presence=PresenceStatus.PRESENT,
        integrity=IntegrityStatus.VERIFIED,
        runtime=RuntimeStatus.BUSY,
        validation=ValidationStatus.SMOKE_PASSED,
        publication=PublicationStatus.PUBLISHED,
        route_available=True,
    )

    assert ready.executable is True
    assert ready.blockers == ()


def test_model_identity_is_content_and_release_based_not_a_windows_path() -> None:
    artifact = ModelArtifactIdentity(
        kind=ArtifactKind.FILE,
        content_sha256="A" * 64,
        size_bytes=17_000_000_000,
        format="GGUF",
    )
    location = ModelArtifactLocation("models-primary", r"ollama\\qwen3.8-27b.gguf")
    component = ModelComponent("qwen3-8-27b-q4", artifact.content_sha256, ComponentRole.PRIMARY_MODEL)
    installation = RuntimeModelInstallation(
        release_code=component.release_code,
        runtime_kind=RuntimeKind.OLLAMA,
        runtime_installation_version_id="runtime-version-ollama-main",
        native_locator="qwen3.8:27b",
    )

    assert location.relative_path == "ollama/qwen3.8-27b.gguf"
    assert installation.native_locator == "qwen3.8:27b"


@pytest.mark.parametrize("path", [r"F:\\AI_Models\\model.gguf", "/mnt/models/model.gguf", "../outside/model.gguf"])
def test_model_locations_reject_absolute_or_escaping_paths(path: str) -> None:
    with pytest.raises(ValueError):
        ModelArtifactLocation("models-primary", path)

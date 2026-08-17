"""P1-8 Ref2V capability bit: manifest gate, build_ref2va compile path, API."""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from local_drama.application.h3_workflows import H3WorkflowFactory
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def test_ref2va_supported_matches_real_manifest(workspace) -> None:
    factory = H3WorkflowFactory(workspace)
    manifest = factory._manifest()
    loader = manifest["authoritative_current_state"]["loader_assets"]
    assert loader["ref2va_unet_name"] == "minimax_h3_ref2va_int8_convrot.safetensors"
    assert factory.ref2va_supported(manifest) is True
    # a manifest without the model key is not supported
    stripped = copy.deepcopy(manifest)
    del stripped["authoritative_current_state"]["loader_assets"]["ref2va_unet_name"]
    assert factory.ref2va_supported(stripped) is False


def test_build_ref2va_compiles_native_chain_when_supported(workspace) -> None:
    factory = H3WorkflowFactory(workspace)
    workflow = factory.build_ref2va("reference identity", first_frame_media_version_id="ref_keyframe.png", seed=11, duration_seconds=4.0)
    assert workflow["1"]["inputs"]["unet_name"] == "minimax_h3_ref2va_int8_convrot.safetensors"
    assert workflow["7"]["class_type"] == "MiniMaxH3ReferenceToVideo"
    inputs = workflow["7"]["inputs"]
    assert inputs["ref_image_0"] == ["6", 0]  # reference image autogrow slot
    assert inputs["audio_vae"] == ["4", 0]  # Ref2V node requires the audio VAE
    assert inputs["length"] == 107
    assert inputs["width"] == 480
    assert inputs["height"] == 832
    assert workflow["16"]["inputs"]["format"] == "mp4"
    # sampler chain mirrors the native pipeline
    assert workflow["10"]["inputs"]["scheduler"] == "simple"
    assert workflow["12"]["inputs"]["latent_image"] == ["7", 1]


def test_build_ref2va_applies_tier(workspace) -> None:
    factory = H3WorkflowFactory(workspace)
    workflow = factory.build_ref2va("reference identity", first_frame_media_version_id="ref_keyframe.png", seed=11, tier="PRODUCTION", aspect_ratio="16:9")
    assert workflow["7"]["inputs"]["length"] == 175
    assert workflow["7"]["inputs"]["width"] == 864
    assert workflow["7"]["inputs"]["height"] == 480


def test_build_ref2va_gates_unverified_video_reference(workspace) -> None:
    factory = H3WorkflowFactory(workspace)
    with pytest.raises(DomainRuleError) as raised:
        factory.build_ref2va("ref", first_frame_media_version_id="kf.png", reference_video_media_version_id="clip.mp4", seed=1)
    assert raised.value.code == "H3_REF2VA_UNAVAILABLE"
    assert raised.value.details["route"] == "native_v2v"


def test_build_ref2va_requires_a_reference_and_safe_path(workspace) -> None:
    factory = H3WorkflowFactory(workspace)
    with pytest.raises(DomainRuleError) as raised:
        factory.build_ref2va("ref", seed=1)
    assert raised.value.code == "H3_REF2VA_REFERENCE_REQUIRED"
    with pytest.raises(DomainRuleError) as raised:
        factory.build_ref2va("ref", first_frame_media_version_id=r"C:\secret\kf.png", seed=1)
    assert raised.value.code == "H3_FIRST_FRAME_INVALID"


def test_build_ref2va_raises_when_manifest_lacks_ref2va_model(workspace, monkeypatch) -> None:
    factory = H3WorkflowFactory(workspace)
    stripped = copy.deepcopy(factory._manifest())
    del stripped["authoritative_current_state"]["loader_assets"]["ref2va_unet_name"]
    monkeypatch.setattr(factory, "_manifest", lambda: stripped)
    assert factory.ref2va_supported(stripped) is False
    with pytest.raises(DomainRuleError) as raised:
        factory.build_ref2va("ref", first_frame_media_version_id="kf.png", seed=1)
    assert raised.value.code == "H3_REF2VA_UNAVAILABLE"
    assert "loader_assets.ref2va_unet_name" in str(raised.value.details.get("missing", ""))


def test_ref2va_capability_endpoint_reflects_real_manifest(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/capabilities/ref2va")
        assert response.status_code == 200
        capability = response.json()["capability"]
        assert capability["supported"] is True
        assert capability["capability"] == "H3_REF2VA_CANDIDATE"
        assert capability["manifest_hint"]["ref2va_unet_name"] == "minimax_h3_ref2va_int8_convrot.safetensors"
        assert capability["manifest_hint"]["node_family"] == "comfy_extras.MiniMaxH3ReferenceToVideo"
        assert capability["reason"]

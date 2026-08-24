from __future__ import annotations

import pytest

from local_drama.application.comfy_jobs import ComfyGenerationService
from local_drama.application.h3_workflows import H3WorkflowFactory
from local_drama.domain.errors import DomainRuleError


def test_t2v_binds_sigma_and_turbo_lora_to_model_chain(workspace) -> None:
    factory = H3WorkflowFactory(workspace)
    workflow = factory.build_t2va(
        "a controlled local shot",
        seed=7,
        sigma_points=24,
        acceleration="TURBO_LORA",
        lora_strength=0.75,
    )

    lora = next(node for node in workflow.values() if node["class_type"] == "LoraLoaderModelOnly")
    assert lora["inputs"]["lora_name"] == "minimax_h3_turbo_v4_step600_ema.safetensors"
    assert lora["inputs"]["strength_model"] == 0.75
    assert workflow["7"]["inputs"]["steps"] == 24
    lora_id = next(node_id for node_id, item in workflow.items() if item is lora)
    assert workflow["7"]["inputs"]["model"] == [lora_id, 0]
    assert workflow["9"]["inputs"]["model"] == workflow["7"]["inputs"]["model"]


def test_i2v_native_audio_false_removes_audio_vae_decode_and_mix(workspace) -> None:
    factory = H3WorkflowFactory(workspace)
    workflow = factory.build_fl2va(
        "a silent local shot",
        first_frame="keyframe.png",
        seed=8,
        native_audio=False,
    )

    assert "4" not in workflow
    assert "14" not in workflow
    assert "audio" not in workflow["15"]["inputs"]


def test_ref2v_fails_closed_when_native_audio_is_disabled(workspace) -> None:
    factory = H3WorkflowFactory(workspace)
    with pytest.raises(DomainRuleError) as raised:
        factory.build_ref2va(
            "a reference shot",
            first_frame_media_version_id="reference.png",
            seed=9,
            native_audio=False,
        )
    assert raised.value.code == "H3_NATIVE_AUDIO_REQUIRED"


def test_acceleration_enum_fails_closed(workspace) -> None:
    with pytest.raises(DomainRuleError) as raised:
        H3WorkflowFactory(workspace).build_t2va("shot", seed=1, acceleration="FAST")
    assert raised.value.code == "H3_ACCELERATION_UNSUPPORTED"


def test_job_compiler_applies_frozen_runtime_snapshot_to_graph(workspace, database) -> None:
    workflow = H3WorkflowFactory(workspace).build_t2va("snapshot shot", seed=2)
    evidence = ComfyGenerationService(database, workspace)._apply_effective_configuration(
        workflow,
        {
            "fingerprint": "sha256:" + "a" * 64,
            "effective_settings": {
                "sigma_points": 31,
                "acceleration": "TURBO_LORA",
                "lora_strength": 0.6,
                "native_audio": False,
            },
        },
    )
    lora_id = evidence["lora_nodes"][0]
    assert workflow["7"]["inputs"]["steps"] == 31
    assert workflow["7"]["inputs"]["model"] == [lora_id, 0]
    assert workflow["9"]["inputs"]["model"] == [lora_id, 0]
    assert workflow[lora_id]["inputs"]["strength_model"] == 0.6
    assert "4" not in workflow and "12" not in workflow
    assert "audio" not in workflow["13"]["inputs"]
    assert evidence["effective_configuration_fingerprint"].startswith("sha256:")

from __future__ import annotations

from local_drama.model_platform.application.quick_create_readiness import (
    _direct_image_contract_blocker,
    _image_to_video_contract_blocker,
)


def test_direct_quick_image_requires_published_comfy_prompt_contract_and_image_output() -> None:
    assert _direct_image_contract_blocker(
        adapter_code="comfy.workflow.v1",
        workflow_status="PUBLISHED",
        workflow_contract={"input_slots": {"PROMPT": {"required": True}, "SEED": {"required": False}}},
        profile_payload={"execution_binding": {"expected_output": {"media_kind": "IMAGE", "min_count": 1, "max_count": 1}}},
    ) is None


def test_direct_quick_image_rejects_extra_required_slots_or_non_image_output() -> None:
    assert _direct_image_contract_blocker(
        adapter_code="comfy.workflow.v1",
        workflow_status="PUBLISHED",
        workflow_contract={"input_slots": {"PROMPT": {"required": True}, "FIRST_FRAME": {"required": True}}},
        profile_payload={"execution_binding": {"expected_output": {"media_kind": "IMAGE"}}},
    ) == "QUICK_CREATE_V2_DIRECT_INPUT_CONTRACT_UNSUPPORTED"
    assert _direct_image_contract_blocker(
        adapter_code="comfy.workflow.v1",
        workflow_status="PUBLISHED",
        workflow_contract={"input_slots": {"PROMPT": {"required": True}}},
        profile_payload={"execution_binding": {"expected_output": {"media_kind": "VIDEO"}}},
    ) == "QUICK_CREATE_V2_IMAGE_OUTPUT_CONTRACT_REQUIRED"


def test_i2v_requires_published_comfy_prompt_first_frame_and_video_output() -> None:
    assert _image_to_video_contract_blocker(
        adapter_code="comfy.workflow.v1",
        workflow_status="PUBLISHED",
        workflow_contract={"input_slots": {"PROMPT": {"required": True}, "FIRST_FRAME": {"required": True}, "SEED": {"required": False}}},
        profile_payload={"execution_binding": {"expected_output": {"media_kind": "VIDEO"}}},
    ) is None
    assert _image_to_video_contract_blocker(
        adapter_code="comfy.workflow.v1",
        workflow_status="PUBLISHED",
        workflow_contract={"input_slots": {"PROMPT": {"required": True}}},
        profile_payload={"execution_binding": {"expected_output": {"media_kind": "VIDEO"}}},
    ) == "QUICK_CREATE_V2_I2V_INPUT_CONTRACT_REQUIRED"
    assert _image_to_video_contract_blocker(
        adapter_code="comfy.workflow.v1",
        workflow_status="PUBLISHED",
        workflow_contract={"input_slots": {"PROMPT": {"required": True}, "FIRST_FRAME": {"required": True}}},
        profile_payload={"execution_binding": {"expected_output": {"media_kind": "IMAGE"}}},
    ) == "QUICK_CREATE_V2_I2V_VIDEO_OUTPUT_REQUIRED"

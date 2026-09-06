from __future__ import annotations

from local_drama.application.workflow_runtime import WorkflowRuntimeService
from local_drama.domain.shot_keyframe_route import SHOT_KEYFRAME_SINGLE_FRAME, validate_shot_keyframe_route

GRAPH = {
    "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen"}},
    "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
    "8": {"class_type": "KSampler", "inputs": {"positive": ["5", 0], "negative": ["6", 0], "seed": 0}},
    "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0]}},
    "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0]}},
}
CONTRACT = {
    "purpose": SHOT_KEYFRAME_SINGLE_FRAME,
    "output_layout": "SINGLE_FRAME",
    "inputs": {
        "PROMPT": {"type": "TEXT", "required": True},
        "NEGATIVE_PROMPT": {"type": "TEXT", "required": True},
        "SEED": {"type": "INTEGER", "required": True},
    },
    "outputs": {"IMAGE": {"type": "IMAGE", "layout": "SINGLE_FRAME", "required": True}},
}
BINDINGS = {
    "PROMPT": {"node_id": "5", "input": "text"},
    "NEGATIVE_PROMPT": {"node_id": "6", "input": "text"},
    "SEED": {"node_id": "8", "input": "seed"},
}


def test_single_frame_route_requires_negative_encoder_binding() -> None:
    route = validate_shot_keyframe_route(
        route_capability=SHOT_KEYFRAME_SINGLE_FRAME,
        profile_capability="IMAGE_CONCEPT",
        workflow_capability="IMAGE_CONCEPT",
        contract=CONTRACT,
        bindings={key: value for key, value in BINDINGS.items() if key != "NEGATIVE_PROMPT"},
        workflow=GRAPH,
    )

    assert route["status"] == "BLOCKED"
    assert "SHOT_KEYFRAME_NEGATIVE_PROMPT_BINDING_REQUIRED" in {item["code"] for item in route["blockers"]}


def test_single_frame_route_accepts_distinct_positive_and_negative_nodes() -> None:
    route = validate_shot_keyframe_route(
        route_capability=SHOT_KEYFRAME_SINGLE_FRAME,
        profile_capability="IMAGE_CONCEPT",
        workflow_capability="IMAGE_CONCEPT",
        contract=CONTRACT,
        bindings=BINDINGS,
        workflow=GRAPH,
    )

    assert route["status"] == "READY"
    assert route["bindings"]["PROMPT"]["node_id"] == "5"
    assert route["bindings"]["NEGATIVE_PROMPT"]["node_id"] == "6"
    assert route["facts"]["save_image_count"] == 1


def test_legacy_profile_binding_is_blocked_even_when_graph_has_a_negative_node() -> None:
    route = validate_shot_keyframe_route(
        route_capability="IMAGE_CONCEPT",
        profile_capability="IMAGE_CONCEPT",
        workflow_capability="IMAGE_CONCEPT",
        contract=CONTRACT,
        bindings=BINDINGS,
        workflow=GRAPH,
    )

    assert route["status"] == "BLOCKED"
    assert "SHOT_KEYFRAME_SINGLE_FRAME_CONTRACT_REQUIRED" in {item["code"] for item in route["blockers"]}


def test_single_frame_route_rejects_multiview_graph_even_when_contract_is_present() -> None:
    route = validate_shot_keyframe_route(
        route_capability=SHOT_KEYFRAME_SINGLE_FRAME,
        profile_capability="IMAGE_MULTI_VIEW",
        workflow_capability="IMAGE_CONCEPT",
        contract=CONTRACT,
        bindings=BINDINGS,
        workflow={**GRAPH, "11": {"class_type": "ImageBatch", "inputs": {"images": ["9", 0]}}},
    )

    assert route["status"] == "BLOCKED"
    assert "SHOT_KEYFRAME_MULTIVIEW_PROFILE_UNSUPPORTED" in {item["code"] for item in route["blockers"]}
    assert "SHOT_KEYFRAME_GRAPH_NOT_SINGLE_FRAME" in {item["code"] for item in route["blockers"]}


def test_workflow_runtime_contract_validation_is_strict_for_single_frame() -> None:
    blocked = WorkflowRuntimeService._validate_contract(
        GRAPH,
        SHOT_KEYFRAME_SINGLE_FRAME,
        CONTRACT,
        {key: value for key, value in BINDINGS.items() if key != "NEGATIVE_PROMPT"},
        [],
    )

    assert blocked["status"] == "BLOCKED"
    assert "SHOT_KEYFRAME_NEGATIVE_PROMPT_BINDING_REQUIRED" in {item["code"] for item in blocked["blockers"]}

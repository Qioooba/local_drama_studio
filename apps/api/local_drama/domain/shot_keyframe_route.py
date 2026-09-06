"""Canonical safety checks for the shot-keyframe image route.

Shot keyframes are single drawable moments.  They must not silently reuse a
character multi-view/contact-sheet route, and a negative prompt must reach the
workflow's negative text encoder as an independent semantic input.  This
module is deliberately pure so the same rules can be used by the visible
contract editor and by the shot-generation preflight without maintaining two
sets of business predicates.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SHOT_KEYFRAME_SINGLE_FRAME = "SHOT_KEYFRAME_SINGLE_FRAME"
CHARACTER_MULTIVIEW = "CHARACTER_MULTIVIEW"

_MULTIVIEW_CAPABILITIES = frozenset({"IMAGE_MULTI_VIEW", CHARACTER_MULTIVIEW})
_REQUIRED_BINDINGS = ("PROMPT", "NEGATIVE_PROMPT", "SEED")
_FORBIDDEN_GRAPH_MARKERS = (
    "multiview",
    "multi_view",
    "multi-view",
    "imagebatch",
    "image_batch",
    "concat",
    "stitch",
    "montage",
    "contactsheet",
    "contact_sheet",
    "panel",
)


def _upper(value: object) -> str:
    return str(value or "").strip().upper()


def _binding_target(binding: object) -> tuple[str, str] | None:
    if not isinstance(binding, Mapping):
        return None
    node_id = str(binding.get("node_id") or "").strip()
    input_name = str(binding.get("input") or "").strip()
    return (node_id, input_name) if node_id and input_name else None


def _declared_output_layout(contract: Mapping[str, Any]) -> str:
    direct = contract.get("output_layout") or contract.get("layout")
    if direct:
        return _upper(direct)
    outputs = contract.get("outputs")
    if isinstance(outputs, Mapping):
        layouts = {
            _upper(spec.get("layout"))
            for spec in outputs.values()
            if isinstance(spec, Mapping) and spec.get("layout")
        }
        if len(layouts) == 1:
            return next(iter(layouts))
    return ""


def _graph_facts(workflow: Mapping[str, Any]) -> tuple[list[dict[str, str]], list[str], list[dict[str, Any]]]:
    targets: list[dict[str, str]] = []
    classes: list[str] = []
    batch_inputs: list[dict[str, Any]] = []
    for raw_node_id, raw_node in workflow.items():
        node_id = str(raw_node_id)
        if not isinstance(raw_node, Mapping):
            continue
        class_type = str(raw_node.get("class_type") or "")
        classes.append(class_type)
        inputs = raw_node.get("inputs")
        if not isinstance(inputs, Mapping):
            continue
        for input_name, value in inputs.items():
            if str(input_name).lower() == "batch_size":
                batch_inputs.append({"node_id": node_id, "input": str(input_name), "value": value})
            if isinstance(value, list) and len(value) == 2:
                targets.append({"node_id": node_id, "input": str(input_name), "source_node_id": str(value[0])})
    return targets, classes, batch_inputs


def validate_shot_keyframe_route(
    *,
    route_capability: object,
    profile_capability: object,
    workflow_capability: object,
    contract: Mapping[str, Any] | None,
    bindings: Mapping[str, Any] | None,
    workflow: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a deterministic READY/BLOCKED decision for shot keyframes.

    ``route_capability`` is supplied by the published Workflow App Contract;
    the profile and workflow capabilities are retained as safety facts.  A
    missing/invalid route is never converted into a generic image fallback.
    """

    normalized_route = _upper(route_capability)
    normalized_profile = _upper(profile_capability)
    normalized_workflow = _upper(workflow_capability)
    parsed_contract = dict(contract) if isinstance(contract, Mapping) else {}
    parsed_bindings = dict(bindings) if isinstance(bindings, Mapping) else {}
    graph = dict(workflow) if isinstance(workflow, Mapping) else {}
    blockers: list[dict[str, str]] = []

    if normalized_profile in _MULTIVIEW_CAPABILITIES or normalized_workflow in _MULTIVIEW_CAPABILITIES:
        blockers.append({
            "code": "SHOT_KEYFRAME_MULTIVIEW_PROFILE_UNSUPPORTED",
            "message": "关键帧首尾帧不能使用角色多视角或联系表工作流",
        })
    if normalized_route != SHOT_KEYFRAME_SINGLE_FRAME:
        blockers.append({
            "code": "SHOT_KEYFRAME_SINGLE_FRAME_CONTRACT_REQUIRED",
            "message": "关键帧首尾帧必须绑定 SHOT_KEYFRAME_SINGLE_FRAME 应用契约",
        })
    if _declared_output_layout(parsed_contract) != "SINGLE_FRAME":
        blockers.append({
            "code": "SHOT_KEYFRAME_SINGLE_FRAME_LAYOUT_REQUIRED",
            "message": "关键帧应用契约必须声明 SINGLE_FRAME 输出布局",
        })

    inputs = parsed_contract.get("inputs")
    if not isinstance(inputs, Mapping):
        inputs = {}
    for role in _REQUIRED_BINDINGS:
        spec = inputs.get(role)
        if not isinstance(spec, Mapping) or spec.get("required", True) is False:
            blockers.append({
                "code": f"SHOT_KEYFRAME_{role}_CONTRACT_REQUIRED",
                "message": f"关键帧应用契约必须声明必需语义输入 {role}",
            })
        if role not in parsed_bindings:
            blockers.append({
                "code": f"SHOT_KEYFRAME_{role}_BINDING_REQUIRED",
                "message": f"关键帧工作流必须独立绑定 {role}",
            })

    for role, binding in parsed_bindings.items():
        target = _binding_target(binding)
        if target is None:
            blockers.append({
                "code": "SHOT_KEYFRAME_BINDING_TARGET_INVALID",
                "message": f"关键帧语义绑定 {role} 缺少有效节点输入",
            })
            continue
        node = graph.get(target[0])
        if not isinstance(node, Mapping) or not isinstance(node.get("inputs"), Mapping) or target[1] not in node["inputs"]:
            blockers.append({
                "code": "SHOT_KEYFRAME_BINDING_TARGET_INVALID",
                "message": f"关键帧语义绑定 {role} 未指向工作流中的有效节点输入",
            })

    prompt_target = _binding_target(parsed_bindings.get("PROMPT"))
    negative_target = _binding_target(parsed_bindings.get("NEGATIVE_PROMPT"))
    if prompt_target is not None and prompt_target == negative_target:
        blockers.append({
            "code": "SHOT_KEYFRAME_PROMPT_BINDINGS_MUST_BE_DISTINCT",
            "message": "PROMPT 与 NEGATIVE_PROMPT 必须指向不同的文本编码节点",
        })

    _targets, classes, batch_inputs = _graph_facts(graph)
    forbidden = sorted({
        class_type
        for class_type in classes
        if any(marker in class_type.lower().replace(" ", "") for marker in _FORBIDDEN_GRAPH_MARKERS)
    })
    if forbidden:
        blockers.append({
            "code": "SHOT_KEYFRAME_GRAPH_NOT_SINGLE_FRAME",
            "message": f"关键帧工作流含多画幅/拼接节点：{', '.join(forbidden)}",
        })
    save_nodes = [class_type for class_type in classes if class_type.lower() == "saveimage"]
    if not save_nodes:
        blockers.append({
            "code": "SHOT_KEYFRAME_OUTPUT_NODE_REQUIRED",
            "message": "关键帧工作流必须声明 SaveImage 输出节点",
        })
    elif len(save_nodes) > 1:
        blockers.append({
            "code": "SHOT_KEYFRAME_OUTPUT_NODE_MULTIPLE",
            "message": "关键帧单画幅工作流只能声明一个 SaveImage 输出节点",
        })
    for item in batch_inputs:
        value = item["value"]
        if isinstance(value, bool) or not isinstance(value, int) or value != 1:
            blockers.append({
                "code": "SHOT_KEYFRAME_BATCH_SIZE_ONE_REQUIRED",
                "message": f"关键帧工作流必须固定单张输出：节点 {item['node_id']} 的 {item['input']} 不是 1",
            })

    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for blocker in blockers:
        if blocker["code"] in seen:
            continue
        seen.add(blocker["code"])
        deduped.append(blocker)
    return {
        "status": "BLOCKED" if deduped else "READY",
        "route_capability": normalized_route,
        "profile_capability": normalized_profile,
        "workflow_capability": normalized_workflow,
        "blockers": deduped,
        "bindings": parsed_bindings,
        "contract": parsed_contract,
        "facts": {
            "output_layout": _declared_output_layout(parsed_contract) or None,
            "save_image_count": len(save_nodes),
            "batch_size_values": batch_inputs,
            "graph_classes": classes,
        },
    }

"""Canonical production and generation specification resolution.

The project production plan owns the requested delivery canvas.  A published
workflow owns the dimensions it can actually generate.  Keeping those two
facts separate is important for local model hosts such as MiniMax H3, whose
verified graph is a proxy-sized 864x480 canvas rather than the requested
delivery canvas.

This module is intentionally pure.  It does not contact a runtime or mutate a
plan; callers use the returned value as read-model evidence and freeze it in a
job/render execution snapshot before executing anything.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any, Mapping

from .errors import DomainRuleError

PRODUCTION_PLAN_SCHEMA_VERSION = "localdrama.production-plan.v2"
PRODUCTION_SPEC_SNAPSHOT_VERSION = "localdrama.production-spec-snapshot.v1"
PRODUCTION_PLAN_CODE_PREFIX = "project-production-plan"
_SUPPORTED_ASPECTS = {"16:9", "9:16"}
_REQUIRED_DIMENSION_ROLES = ("WIDTH", "HEIGHT", "FPS")
_SUPPORTED_COMPOSITION_POLICIES = {"LETTERBOX", "COVER", "CROP"}


def canonical_production_plan_code(project_id: str) -> str:
    """Return the stable, project-scoped identity for the canonical plan.

    ``production_plans.code`` is globally unique, while a production plan is
    edited as the single current plan for one project.  A client-provided
    display/intention code must therefore never encode a transient aspect or
    resolution (and must not be allowed to collide with another project).
    The project id is the durable identity component; the caller's code is
    retained in the audit event by the application layer.
    """

    identity = "".join(character.lower() if character.isalnum() else "-" for character in str(project_id)).strip("-")
    if not identity:
        raise DomainRuleError("INVALID_PRODUCTION_PLAN", "project_id 不能为空")
    return f"{PRODUCTION_PLAN_CODE_PREFIX}-{identity}"


def _as_object(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise DomainRuleError("PRODUCTION_SPEC_INVALID", f"{name} 必须是正整数")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise DomainRuleError("PRODUCTION_SPEC_INVALID", f"{name} 必须是正整数") from error
    if result <= 0:
        raise DomainRuleError("PRODUCTION_SPEC_INVALID", f"{name} 必须是正整数")
    return result


def _fps(value: Any) -> tuple[dict[str, int], float]:
    if isinstance(value, Mapping):
        numerator = _positive_int(value.get("numerator"), "fps.numerator")
        denominator = _positive_int(value.get("denominator", 1), "fps.denominator")
    elif isinstance(value, str) and "/" in value:
        raw_numerator, raw_denominator = value.split("/", 1)
        numerator = _positive_int(raw_numerator.strip(), "fps.numerator")
        denominator = _positive_int(raw_denominator.strip(), "fps.denominator")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        fraction = Fraction(value).limit_denominator(1000)
        numerator, denominator = fraction.numerator, fraction.denominator
    else:
        raise DomainRuleError("PRODUCTION_SPEC_INVALID", "fps 必须是正数或有理数")
    fraction = Fraction(numerator, denominator)
    if fraction <= 0 or fraction > 120:
        raise DomainRuleError("PRODUCTION_SPEC_INVALID", "fps 必须在 0—120 之间")
    return {"numerator": fraction.numerator, "denominator": fraction.denominator}, float(fraction)


def canonical_presentation(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate and normalize the delivery portion of a production plan."""

    raw = _as_object(value)
    width = _positive_int(raw.get("width"), "presentation.width")
    height = _positive_int(raw.get("height"), "presentation.height")
    if width < 64 or height < 64 or width % 2 or height % 2:
        raise DomainRuleError(
            "PRODUCTION_SPEC_INVALID",
            "presentation.width/height 必须是 64 以上的偶数",
            {"width": width, "height": height},
        )
    aspect_ratio = str(raw.get("aspect_ratio") or "").strip()
    if aspect_ratio not in _SUPPORTED_ASPECTS:
        raise DomainRuleError(
            "PRODUCTION_ASPECT_UNSUPPORTED",
            "当前生产计划只支持 16:9 或 9:16",
            {"aspect_ratio": aspect_ratio, "supported": sorted(_SUPPORTED_ASPECTS)},
        )
    # Common encoder presets (notably 480P) use the nearest even integer for
    # one side of the canvas.  For example, 9:16 becomes 480x854 because the
    # mathematically exact height is 853.333... pixels.  Compare against the
    # declared ratio using a one-pixel rounding budget instead of requiring an
    # impossible exact integer cross-product.  The budget still rejects any
    # meaningful composition mismatch (for example 480x850).
    ratio_numerator, ratio_denominator = (int(part) for part in aspect_ratio.split(":", 1))
    expected_height = Fraction(width * ratio_denominator, ratio_numerator)
    if abs(Fraction(height) - expected_height) > 1:
        raise DomainRuleError(
            "PRODUCTION_ASPECT_DIMENSION_MISMATCH",
            "presentation 的画幅与宽高不一致",
            {"aspect_ratio": aspect_ratio, "width": width, "height": height},
        )
    fps_value = raw.get("fps")
    if fps_value is None:
        raise DomainRuleError("PRODUCTION_SPEC_INVALID", "生产计划必须显式声明 fps")
    fps, _fps_value = _fps(fps_value)
    return {"aspect_ratio": aspect_ratio, "width": width, "height": height, "fps": fps}


def canonical_production_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Return the only persisted shape accepted for an explicit production plan."""

    raw = _as_object(plan)
    presentation_raw = raw.get("presentation")
    if not isinstance(presentation_raw, Mapping):
        # Keep old callers readable while they migrate.  New explicit plans
        # are always written under ``presentation`` by the settings editor.
        presentation_raw = raw
    presentation = canonical_presentation(presentation_raw)
    generation = _as_object(raw.get("generation"))
    upscale = _as_object(generation.get("upscale"))
    normalized_upscale = {
        "enabled": bool(upscale.get("enabled", upscale.get("required", False))),
        "required": bool(upscale.get("required", upscale.get("enabled", False))),
        "stage": str(upscale.get("stage") or "COMPOSE_QC"),
        "executor": str(upscale.get("executor") or "builtin:ffmpeg"),
        "target": str(upscale.get("target") or "PRESENTATION_SPEC"),
    }
    fit = str(upscale.get("fit") or "").strip().upper()
    if fit and fit not in _SUPPORTED_COMPOSITION_POLICIES:
        raise DomainRuleError(
            "PRODUCTION_COMPOSITION_POLICY_INVALID",
            "构图策略必须是 LETTERBOX、COVER 或 CROP",
            {"fit": fit, "supported": sorted(_SUPPORTED_COMPOSITION_POLICIES)},
        )
    if fit:
        normalized_upscale["fit"] = fit
    return {
        "schema_version": str(raw.get("schema_version") or PRODUCTION_PLAN_SCHEMA_VERSION),
        "presentation": presentation,
        "generation": {
            "strategy": str(generation.get("strategy") or "CAPABILITY_RESOLVED"),
            "upscale": normalized_upscale,
        },
    }


def _graph_geometry(workflow_content: Mapping[str, Any]) -> tuple[int, int, float] | None:
    """Find the authored video geometry without contacting ComfyUI."""

    candidates: list[tuple[int, int, float, int]] = []
    graph_fps = 0.0
    for raw_node in workflow_content.values():
        node = _as_object(raw_node)
        if str(node.get("class_type") or "") != "CreateVideo":
            continue
        inputs = _as_object(node.get("inputs"))
        if inputs.get("fps") is None:
            continue
        try:
            _fps_ratio, graph_fps = _fps(inputs["fps"])
        except DomainRuleError:
            graph_fps = 0.0
        if graph_fps > 0:
            break
    for _node_id, raw_node in workflow_content.items():
        node = _as_object(raw_node)
        inputs = _as_object(node.get("inputs"))
        if "width" not in inputs or "height" not in inputs:
            continue
        try:
            width = _positive_int(inputs["width"], "workflow.width")
            height = _positive_int(inputs["height"], "workflow.height")
        except DomainRuleError:
            continue
        class_type = str(node.get("class_type") or "")
        priority = 3 if "Video" in class_type or "video" in class_type else 1
        fps = 0.0
        if class_type == "CreateVideo" and inputs.get("fps") is not None:
            try:
                _fps_ratio, fps = _fps(inputs["fps"])
            except DomainRuleError:
                fps = 0.0
            priority = 4
        candidates.append((width, height, fps, priority))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[3], reverse=True)
    width, height, fps, _priority = candidates[0]
    if fps <= 0:
        fps = graph_fps
    if fps <= 0:
        # A video graph without a declared CreateVideo rate cannot claim to
        # satisfy the delivery rate.  The caller will fail closed.
        return width, height, 0.0
    return width, height, fps


def _same_fps(left: float, right: float) -> bool:
    return abs(left - right) < 0.0001


def _upscale_path(plan: Mapping[str, Any]) -> dict[str, Any] | None:
    generation = _as_object(plan.get("generation"))
    upscale = _as_object(generation.get("upscale"))
    enabled = bool(upscale.get("enabled", upscale.get("required", False)))
    stage = str(upscale.get("stage") or "").strip().upper()
    executor = str(upscale.get("executor") or "").strip()
    target = str(upscale.get("target") or "").strip().upper()
    if not enabled or stage != "COMPOSE_QC" or executor != "builtin:ffmpeg" or target != "PRESENTATION_SPEC":
        return None
    fit = str(upscale.get("fit") or "").strip().upper()
    if fit and fit not in _SUPPORTED_COMPOSITION_POLICIES:
        return None
    result = {
        "enabled": True,
        "required": bool(upscale.get("required", True)),
        "stage": "COMPOSE_QC",
        "executor": "builtin:ffmpeg",
        "target": "PRESENTATION_SPEC",
    }
    if fit:
        result["fit"] = fit
    return result


def resolve_production_spec(
    plan: Mapping[str, Any],
    workflow_bindings: Mapping[str, Any] | None,
    workflow_content: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Resolve delivery and executable generation facts for one workflow.

    The result is suitable for both the read model and an immutable execution
    snapshot.  A missing or partial size binding is never silently converted
    to a direct graph mutation: it is an actionable BLOCKED result.
    """

    canonical_plan = canonical_production_plan(plan)
    delivery = canonical_plan["presentation"]
    bindings = _as_object(workflow_bindings)
    roles = {str(role).upper() for role in bindings}
    required = set(_REQUIRED_DIMENSION_ROLES)
    missing_roles = sorted(required - roles) if roles.intersection(required) and not required.issubset(roles) else []
    # A graph may expose one semantic role (H3 currently exposes FPS) while
    # keeping its dimensions fixed in the published content.  In that case
    # the fixed graph geometry plus the audited COMPOSE_QC path is sufficient;
    # block only when the missing role cannot be resolved from graph content.
    geometry = _graph_geometry(_as_object(workflow_content)) if missing_roles else None
    if missing_roles and geometry is None:
        return {
            "schema_version": PRODUCTION_SPEC_SNAPSHOT_VERSION,
            "status": "BLOCKED",
            "delivery": delivery,
            "generation": {"mode": "BLOCKED", "semantic_inputs": {}, "actual": None, "upscale": None},
            "blockers": [{"code": "WORKFLOW_PRODUCTION_SPEC_BINDINGS_INCOMPLETE", "message": "VIDEO workflow 的 WIDTH/HEIGHT/FPS 语义绑定不完整，且 graph 没有固定的可审计规格", "missing_roles": missing_roles}],
            "warnings": [],
        }

    if required.issubset(roles):
        fps, _fps_value = _fps(delivery["fps"])
        semantic_inputs = {"WIDTH": delivery["width"], "HEIGHT": delivery["height"], "FPS": float(Fraction(fps["numerator"], fps["denominator"]))}
        return {
            "schema_version": PRODUCTION_SPEC_SNAPSHOT_VERSION,
            "status": "READY",
            "delivery": delivery,
            "generation": {
                "mode": "NATIVE_SEMANTIC",
                "semantic_inputs": semantic_inputs,
                "actual": {"width": delivery["width"], "height": delivery["height"], "fps": semantic_inputs["FPS"]},
                "upscale": None,
                "workflow_roles": sorted(roles),
            },
            "blockers": [],
            "warnings": [],
        }

    if geometry is None:
        geometry = _graph_geometry(_as_object(workflow_content))
    if geometry is None:
        return {
            "schema_version": PRODUCTION_SPEC_SNAPSHOT_VERSION,
            "status": "BLOCKED",
            "delivery": delivery,
            "generation": {"mode": "BLOCKED", "semantic_inputs": {}, "actual": None, "upscale": None},
            "blockers": [{"code": "WORKFLOW_PRODUCTION_GEOMETRY_UNDECLARED", "message": "VIDEO workflow 未声明可审计的宽高与帧率，无法规划目标交付画布"}],
            "warnings": [],
        }
    actual_width, actual_height, actual_fps = geometry
    if actual_fps <= 0:
        return {
            "schema_version": PRODUCTION_SPEC_SNAPSHOT_VERSION,
            "status": "BLOCKED",
            "delivery": delivery,
            "generation": {"mode": "BLOCKED", "semantic_inputs": {}, "actual": {"width": actual_width, "height": actual_height, "fps": None}, "upscale": None},
            "blockers": [{"code": "WORKFLOW_PRODUCTION_FPS_UNDECLARED", "message": "VIDEO workflow 未声明可审计的输出帧率"}],
            "warnings": [],
        }

    matches = actual_width == delivery["width"] and actual_height == delivery["height"] and _same_fps(actual_fps, float(Fraction(delivery["fps"]["numerator"], delivery["fps"]["denominator"])))
    actual = {"width": actual_width, "height": actual_height, "fps": actual_fps}
    semantic_inputs = {
        role: (delivery["width"] if role == "WIDTH" else delivery["height"] if role == "HEIGHT" else float(Fraction(delivery["fps"]["numerator"], delivery["fps"]["denominator"])))
        for role in sorted(roles.intersection(required))
    }
    binding_warning = []
    if missing_roles:
        binding_warning.append(
            "VIDEO workflow 未声明完整 WIDTH/HEIGHT/FPS semantic bindings；缺失角色使用已发布 graph 固定规格"
        )
    if matches:
        return {
            "schema_version": PRODUCTION_SPEC_SNAPSHOT_VERSION,
            "status": "READY",
            "delivery": delivery,
            "generation": {"mode": "GRAPH_NATIVE_LOCKED", "semantic_inputs": semantic_inputs, "actual": actual, "upscale": None, "workflow_roles": sorted(roles)},
            "blockers": [],
            "warnings": binding_warning or ["当前 workflow 未声明 WIDTH/HEIGHT/FPS semantic bindings，实际规格由已发布 graph 冻结"],
        }

    upscale = _upscale_path(canonical_plan)
    delivery_ratio = delivery["width"] / delivery["height"]
    actual_ratio = actual_width / actual_height
    if abs(delivery_ratio - actual_ratio) > 0.0001 and (upscale is None or "fit" not in upscale):
        return {
            "schema_version": PRODUCTION_SPEC_SNAPSHOT_VERSION,
            "status": "BLOCKED",
            "delivery": delivery,
            "generation": {"mode": "BLOCKED", "semantic_inputs": semantic_inputs, "actual": actual, "upscale": None, "workflow_roles": sorted(roles)},
            "blockers": [{"code": "PRODUCTION_COMPOSITION_POLICY_REQUIRED", "message": f"当前 workflow 画幅 {actual_width}×{actual_height} 与交付 {delivery['width']}×{delivery['height']} 不一致；请在生产计划明确 LETTERBOX、COVER 或 CROP 构图策略"}],
            "warnings": [],
        }
    if upscale is None:
        return {
            "schema_version": PRODUCTION_SPEC_SNAPSHOT_VERSION,
            "status": "BLOCKED",
            "delivery": delivery,
            "generation": {"mode": "BLOCKED", "semantic_inputs": semantic_inputs, "actual": actual, "upscale": None, "workflow_roles": sorted(roles)},
            "blockers": [{"code": "PRODUCTION_UPSCALE_PATH_REQUIRED", "message": f"当前 workflow 实际生成 {actual_width}×{actual_height} @ {actual_fps:g}fps，未声明可审计的 COMPOSE_QC 放大路径；请配置 builtin:ffmpeg → PRESENTATION_SPEC"}],
            "warnings": [],
        }
    return {
        "schema_version": PRODUCTION_SPEC_SNAPSHOT_VERSION,
        "status": "READY",
        "delivery": delivery,
        "generation": {"mode": "UPSCALE_COMPOSE", "semantic_inputs": semantic_inputs, "actual": actual, "upscale": upscale, "workflow_roles": sorted(roles)},
        "blockers": [],
        "warnings": binding_warning + [f"当前 VIDEO workflow 实际生成 {actual_width}×{actual_height} @ {actual_fps:g}fps，最终交付由 COMPOSE_QC 放大到 {delivery['width']}×{delivery['height']}"],
    }

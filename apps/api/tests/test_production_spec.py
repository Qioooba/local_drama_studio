from __future__ import annotations

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.production_spec import canonical_production_plan, resolve_production_spec


def _plan(
    *,
    upscale: bool = True,
    fit: str | None = None,
    width: int = 2560,
    height: int = 1440,
    aspect_ratio: str = "16:9",
) -> dict[str, object]:
    upscale_plan: dict[str, object] = {
        "enabled": upscale,
        "required": upscale,
        "stage": "COMPOSE_QC",
        "executor": "builtin:ffmpeg",
        "target": "PRESENTATION_SPEC",
    }
    if fit is not None:
        upscale_plan["fit"] = fit
    return {
        "schema_version": "localdrama.production-plan.v2",
        "presentation": {
            "aspect_ratio": aspect_ratio,
            "width": width,
            "height": height,
            "fps": {"numerator": 24, "denominator": 1},
        },
        "generation": {
            "strategy": "CAPABILITY_RESOLVED",
            "upscale": upscale_plan,
        },
    }


def _h3_graph() -> dict[str, object]:
    return {
        "6": {"class_type": "ImageScale", "inputs": {"width": 864, "height": 480}},
        "7": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"width": 864, "height": 480, "length": 107}},
        "15": {"class_type": "CreateVideo", "inputs": {"fps": 24.0}},
    }


def _compatible_landscape_graph() -> dict[str, object]:
    return {
        "6": {"class_type": "ImageScale", "inputs": {"width": 864, "height": 486}},
        "7": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"width": 864, "height": 486, "length": 107}},
        "15": {"class_type": "CreateVideo", "inputs": {"fps": 24.0}},
    }


def _portrait_graph() -> dict[str, object]:
    return {
        "6": {"class_type": "ImageScale", "inputs": {"width": 480, "height": 832}},
        "7": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"width": 480, "height": 832, "length": 107}},
        "15": {"class_type": "CreateVideo", "inputs": {"fps": 24.0}},
    }


def test_canonical_plan_normalizes_rational_fps_and_preserves_single_presentation_source() -> None:
    result = canonical_production_plan(_plan())
    assert result["presentation"] == {
        "aspect_ratio": "16:9",
        "width": 2560,
        "height": 1440,
        "fps": {"numerator": 24, "denominator": 1},
    }
    assert result["generation"]["upscale"]["executor"] == "builtin:ffmpeg"  # type: ignore[index]


@pytest.mark.parametrize(
    ("width", "height", "aspect_ratio"),
    [(480, 854, "9:16"), (854, 480, "16:9")],
)
def test_standard_integer_rounded_delivery_dimensions_are_accepted(
    width: int,
    height: int,
    aspect_ratio: str,
) -> None:
    result = canonical_production_plan(
        _plan(width=width, height=height, aspect_ratio=aspect_ratio),
    )
    assert result["presentation"]["width"] == width  # type: ignore[index]
    assert result["presentation"]["height"] == height  # type: ignore[index]
    assert result["presentation"]["aspect_ratio"] == aspect_ratio  # type: ignore[index]


def test_h3_resolves_proxy_generation_and_auditable_compose_upscale() -> None:
    result = resolve_production_spec(_plan(fit="LETTERBOX"), {"PROMPT": {"node_id": "7", "input": "prompt"}}, _h3_graph())
    assert result["status"] == "READY"
    generation = result["generation"]
    assert generation["mode"] == "UPSCALE_COMPOSE"
    assert generation["actual"] == {"width": 864, "height": 480, "fps": 24.0}
    assert generation["upscale"] == {
        "enabled": True,
        "required": True,
        "stage": "COMPOSE_QC",
        "executor": "builtin:ffmpeg",
        "target": "PRESENTATION_SPEC",
        "fit": "LETTERBOX",
    }


def test_missing_upscale_path_blocks_instead_of_claiming_native_2k() -> None:
    result = resolve_production_spec(_plan(upscale=False), {}, _compatible_landscape_graph())
    assert result["status"] == "BLOCKED"
    assert result["blockers"][0]["code"] == "PRODUCTION_UPSCALE_PATH_REQUIRED"  # type: ignore[index]


def test_complete_dimension_semantic_bindings_inject_requested_delivery_spec() -> None:
    bindings = {
        "WIDTH": {"node_id": "7", "input": "width"},
        "HEIGHT": {"node_id": "7", "input": "height"},
        "FPS": {"node_id": "15", "input": "fps"},
    }
    result = resolve_production_spec(_plan(), bindings, _h3_graph())
    assert result["status"] == "READY"
    assert result["generation"]["mode"] == "NATIVE_SEMANTIC"  # type: ignore[index]
    assert result["generation"]["semantic_inputs"] == {"WIDTH": 2560, "HEIGHT": 1440, "FPS": 24.0}  # type: ignore[index]


def test_partial_dimension_semantic_bindings_use_fixed_graph_with_audited_upscale() -> None:
    result = resolve_production_spec(
        _plan(fit="LETTERBOX"),
        {"WIDTH": {"node_id": "7", "input": "width"}},
        _h3_graph(),
    )
    assert result["status"] == "READY"
    assert result["generation"]["mode"] == "UPSCALE_COMPOSE"  # type: ignore[index]
    assert result["generation"]["semantic_inputs"] == {"WIDTH": 2560}  # type: ignore[index]
    assert result["warnings"][0].startswith("VIDEO workflow 未声明完整")


def test_partial_dimension_bindings_without_fixed_graph_fail_closed() -> None:
    result = resolve_production_spec(
        _plan(),
        {"WIDTH": {"node_id": "7", "input": "width"}},
        {"7": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"width": ["node", 0]}}},
    )
    assert result["status"] == "BLOCKED"
    assert result["blockers"][0]["missing_roles"] == ["FPS", "HEIGHT"]  # type: ignore[index]


def test_mismatched_aspect_requires_explicit_composition_policy() -> None:
    blocked = resolve_production_spec(_plan(), {}, _portrait_graph())
    assert blocked["status"] == "BLOCKED"
    assert blocked["blockers"][0]["code"] == "PRODUCTION_COMPOSITION_POLICY_REQUIRED"  # type: ignore[index]

    ready = resolve_production_spec(_plan(fit="LETTERBOX"), {}, _portrait_graph())
    assert ready["status"] == "READY"
    assert ready["generation"]["mode"] == "UPSCALE_COMPOSE"  # type: ignore[index]
    assert ready["generation"]["upscale"]["fit"] == "LETTERBOX"  # type: ignore[index]


def test_invalid_composition_policy_is_rejected_before_resolution() -> None:
    plan = _plan(fit="STRETCH")
    with pytest.raises(DomainRuleError) as error:
        canonical_production_plan(plan)
    assert error.value.code == "PRODUCTION_COMPOSITION_POLICY_INVALID"


def test_invalid_presentation_is_rejected() -> None:
    plan = _plan()
    plan["presentation"]["height"] = 1080  # type: ignore[index]
    with pytest.raises(DomainRuleError) as error:
        canonical_production_plan(plan)
    assert error.value.code == "PRODUCTION_ASPECT_DIMENSION_MISMATCH"

    rounded_but_incompatible = _plan(width=480, height=850, aspect_ratio="9:16")
    with pytest.raises(DomainRuleError) as error:
        canonical_production_plan(rounded_but_incompatible)
    assert error.value.code == "PRODUCTION_ASPECT_DIMENSION_MISMATCH"

from __future__ import annotations

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.shot_prompt_bundle import (
    DEFAULT_SHOT_NEGATIVE_PROMPT,
    PROMPT_COMPILER_AVOID_FALLBACK,
    PROMPT_COMPILER_WORKFLOW_NEGATIVE,
    PROMPT_FRAME_REFRAME_SINGLE_MOMENT,
    apply_prompt_bundle_to_parameters,
    compile_shot_prompt_bundle,
)


def test_prompt_bundle_merges_visible_avoid_constraints_when_workflow_has_no_negative_binding() -> None:
    bundle = compile_shot_prompt_bundle(
        {
            "base_prompt": "镜头 S001，雨夜义庄，沈砚触碰铜灯",
            "positive_override": "single shot, full-frame",
            "negative_prompt": "triptych, contact sheet, split-screen",
            "provenance": "PAGE_USER_EDIT",
        },
        workflow_bindings={"PROMPT": {"node_id": "1", "input": "prompt"}},
    )

    assert bundle["compiler_mode"] == PROMPT_COMPILER_AVOID_FALLBACK
    assert bundle["provenance"] == "PAGE_USER_EDIT"
    assert "single shot, full-frame" in bundle["final_prompt"]
    assert "Avoid: triptych, contact sheet, split-screen" in bundle["final_prompt"]
    parameters = apply_prompt_bundle_to_parameters({"SEED": 7, "NEGATIVE_PROMPT": "stale"}, bundle, {"PROMPT": {}})
    assert parameters["PROMPT"] == bundle["final_prompt"]
    assert "NEGATIVE_PROMPT" not in parameters


def test_prompt_bundle_uses_independent_negative_binding_when_published() -> None:
    bundle = compile_shot_prompt_bundle(
        {"base_prompt": "单一连续镜头", "negative_prompt": "triptych", "provenance": "AI_GENERATED"},
        workflow_bindings={"PROMPT": {"node_id": "1"}, "NEGATIVE_PROMPT": {"node_id": "2"}},
    )

    assert bundle["compiler_mode"] == PROMPT_COMPILER_WORKFLOW_NEGATIVE
    assert "Avoid:" not in bundle["final_prompt"]
    parameters = apply_prompt_bundle_to_parameters({"SEED": 11}, bundle, {"PROMPT": {}, "NEGATIVE_PROMPT": {}})
    assert parameters["PROMPT"] == bundle["final_prompt"]
    assert parameters["NEGATIVE_PROMPT"] == "triptych"


def test_prompt_bundle_defaults_negative_constraints_and_rejects_missing_base_prompt() -> None:
    bundle = compile_shot_prompt_bundle({"base_prompt": "单帧电影画面"})
    assert bundle["negative_prompt"] == DEFAULT_SHOT_NEGATIVE_PROMPT
    with pytest.raises(DomainRuleError) as error:
        compile_shot_prompt_bundle({"positive_override": "only an override"})
    assert error.value.code == "SHOT_PROMPT_BASE_REQUIRED"


def test_prompt_bundle_preserves_source_base_while_executing_role_specific_effective_prompt() -> None:
    source = "镜头 S001，镜头扫过干裂田地。切至药铺门前，沈砚整理灯心草。"
    effective = "镜头 S001；单一画面重构；首帧只呈现第一个视觉瞬间：干裂田地"
    bundle = compile_shot_prompt_bundle(
        {
            "base_prompt": source,
            "negative_prompt": "triptych, contact sheet",
            "provenance": "AI_GENERATED",
            "frame_reframe_mode": PROMPT_FRAME_REFRAME_SINGLE_MOMENT,
        },
        base_prompt=source,
        effective_base_prompt=effective,
        frame_role="FIRST_FRAME",
        workflow_bindings={"PROMPT": {"node_id": "1"}},
    )

    assert bundle["base_prompt"] == source
    assert bundle["effective_base_prompt"] == effective
    assert bundle["frame_role"] == "FIRST_FRAME"
    assert bundle["frame_reframe_mode"] == PROMPT_FRAME_REFRAME_SINGLE_MOMENT
    assert source not in bundle["final_prompt"]
    assert effective in bundle["final_prompt"]
    assert "Avoid: triptych, contact sheet" in bundle["final_prompt"]

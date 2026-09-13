import pytest

from local_drama.application.shot_production_normalizer import ShotProductionSpecNormalizer
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.policies import missing_shot_fields


def test_empty_fields_only_receive_technical_defaults_and_remain_not_ready():
    raw = {}
    normalized = ShotProductionSpecNormalizer.normalize_fields(raw)
    assert normalized["schema_version"] == "director-intent.v3"
    assert normalized["shot_type"] == "MEDIUM_SHOT"
    assert normalized["composition"]["preset"] == "RULE_OF_THIRDS"
    assert normalized["camera_plan"]["mode"] == "PROMPT_FALLBACK"
    assert normalized["camera_plan"]["movement"] == "STATIC"
    assert normalized["target_duration_ms"] == 4000
    assert missing_shot_fields(normalized) == ["subject_action", "continuity", "creative_intent"]


def test_llm_script_breakdown_raw_fields_normalized():
    raw = {
        "visual": "沈砚在药铺柜台前仔细分拣灯心草，神情凝重",
        "action": "低头翻找草药，手指微颤",
        "dialogue": "这味药，决不能出差错。",
        "duration_seconds": 3.5,
        "scene": "古色古香的药铺，光线昏黄",
    }
    normalized = ShotProductionSpecNormalizer.normalize_fields(raw)
    assert normalized["subject_action"] == "低头翻找草药，手指微颤"
    assert normalized["target_duration_ms"] == 3500
    assert normalized["camera_plan"]["prompt_text"] == "固定机位，保持构图稳定"
    assert missing_shot_fields(normalized) == ["continuity"]


def test_chinese_shot_type_and_movement_parsed():
    raw = {
        "shot_type": "特写",
        "camera_plan": "缓慢推镜头，对准手部细节",
        "action": "双手捧起照骨灯",
    }
    normalized = ShotProductionSpecNormalizer.normalize_fields(raw)
    assert normalized["shot_type"] == "CLOSE_UP"
    assert normalized["camera_plan"]["movement"] == "SLOW_PUSH"
    assert normalized["camera_plan"]["direction"] == "FORWARD"
    assert missing_shot_fields(normalized) == ["continuity"]


def test_ensure_ready_returns_clean_dict():
    raw = {"action": "开门走出房间", "continuity": "延续上一镜服装与站位"}
    ready = ShotProductionSpecNormalizer.ensure_ready(raw)
    assert ready["subject_action"] == "开门走出房间"
    assert ready["target_duration_ms"] > 0
    assert missing_shot_fields(ready) == []


def test_ensure_ready_rejects_missing_creative_decisions():
    with pytest.raises(DomainRuleError) as caught:
        ShotProductionSpecNormalizer.ensure_ready({})

    assert caught.value.code == "SHOT_NOT_PRODUCTION_READY"


@pytest.mark.parametrize("intensity", [0, 0.2, 0.9])
def test_performance_intensity_preserves_explicit_valid_value_and_is_idempotent(intensity):
    raw = {
        "action": "角色安静地等待",
        "performance": {"emotion": "克制", "intensity": intensity},
    }

    normalized = ShotProductionSpecNormalizer.normalize_fields(raw)
    normalized_again = ShotProductionSpecNormalizer.normalize_fields(normalized)

    assert normalized["performance"]["intensity"] == intensity
    assert normalized_again == normalized


@pytest.mark.parametrize("intensity", [-0.1, 1.1, True, "0.5"])
def test_performance_intensity_rejects_invalid_explicit_value(intensity):
    with pytest.raises(DomainRuleError) as caught:
        ShotProductionSpecNormalizer.normalize_fields({"action": "角色等待", "performance": {"intensity": intensity}})

    assert caught.value.code == "DIRECTOR_INTENT_INTENSITY_INVALID"


@pytest.mark.parametrize("action", ["推门进入房间", "拉椅子坐下", "摇头拒绝提议"])
def test_subject_action_words_do_not_invent_camera_movement(action):
    normalized = ShotProductionSpecNormalizer.normalize_fields({"action": action})

    assert normalized["subject_action"] == action
    assert normalized["camera_plan"]["movement"] == "STATIC"
    assert normalized["camera_plan"]["direction"] == "UNSPECIFIED"

import pytest
from local_drama.application.shot_production_normalizer import ShotProductionSpecNormalizer
from local_drama.domain.policies import missing_shot_fields, validate_shot_ready


def test_empty_fields_self_heals_to_production_ready():
    raw = {}
    normalized = ShotProductionSpecNormalizer.normalize_fields(raw)
    assert normalized["schema_version"] == "director-intent.v3"
    assert normalized["shot_type"] == "MEDIUM_SHOT"
    assert normalized["composition"]["preset"] == "RULE_OF_THIRDS"
    assert normalized["camera_plan"]["mode"] == "PROMPT_FALLBACK"
    assert normalized["camera_plan"]["movement"] == "SLOW_PUSH"
    assert normalized["target_duration_ms"] == 4000
    assert missing_shot_fields(normalized) == []
    validate_shot_ready(normalized)


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
    assert "灯心草" in normalized["camera_plan"]["prompt_text"] or "运镜" in normalized["camera_plan"]["prompt_text"]
    assert missing_shot_fields(normalized) == []
    validate_shot_ready(normalized)


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
    assert missing_shot_fields(normalized) == []
    validate_shot_ready(normalized)


def test_ensure_ready_returns_clean_dict():
    raw = {"action": "开门走出房间"}
    ready = ShotProductionSpecNormalizer.ensure_ready(raw)
    assert ready["subject_action"] == "开门走出房间"
    assert ready["target_duration_ms"] > 0
    assert missing_shot_fields(ready) == []

from __future__ import annotations

from local_drama.domain.director_intent import normalize_director_intent_v3, validate_director_intent_v3_payload
from local_drama.domain.shot_prompt import compose_shot_prompt, normalize_prompt_modifiers


def test_prompt_modifiers_are_normalized_and_compiled_as_a_final_layer() -> None:
    assert normalize_prompt_modifiers("雨夜，冷色调, 雨夜") == ["雨夜", "冷色调"]
    fields = normalize_director_intent_v3(
        {
            "subject_action": "主角推门进入",
            "creative_intent": "压迫感",
            "shot_type": "MEDIUM",
            "composition": {"preset": "RULE_OF_THIRDS"},
            "performance": {"emotion": "警惕"},
            "camera_plan": {"movement": "DOLLY_IN"},
            "environment": "废弃车站",
            "dialogue": [{"speaker": "主角", "text": "有人吗？"}],
            "prompt_modifiers": ["雨夜", "冷色调", "雨夜"],
        }
    )
    validate_director_intent_v3_payload(fields)
    assert fields["prompt_modifiers"] == ["雨夜", "冷色调"]
    assert compose_shot_prompt(fields, shot_code="S001") == (
        "镜头 S001，主角推门进入，情绪基调：压迫感，景别 MEDIUM，构图 RULE_OF_THIRDS，"
        "运镜 DOLLY_IN，情绪 警惕，环境：废弃车站，对白：有人吗？，统一视觉修饰：雨夜，冷色调"
    )

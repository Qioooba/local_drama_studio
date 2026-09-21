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
        "运镜 DOLLY_IN，情绪 警惕，环境：废弃车站，对白：主角：有人吗？，统一视觉修饰：雨夜，冷色调"
    )


def test_prompt_includes_camera_fallback_instruction() -> None:
    prompt = compose_shot_prompt(
        {
            "subject_action": "人物沿河行走",
            "camera_plan": {
                "movement": "TRACKING",
                "prompt_text": "平稳跟随人物背影，不要定格",
            },
        }
    )

    assert prompt == "人物沿河行走，运镜 TRACKING，运镜说明：平稳跟随人物背影，不要定格"


def test_structured_dialogue_preserves_speaker_and_verbatim_text() -> None:
    dialogue = [
        {"speaker": "A", "text": "别走。"},
        {"speaker": "B", "text": '"我会回来：明天。"\n不要等我。'},
        {"speaker": "旁白", "text": "雨仍在下。"},
        {"speaker": "A", "text": "A：已有标签。"},
        {"speaker": "", "text": "说话人待确认，但正文不能变化。"},
        {"speaker": "不能成为对白", "text": ""},
    ]

    prompt = compose_shot_prompt({"dialogue": dialogue})

    assert prompt == ('对白：A：别走。；B："我会回来：明天。"\n不要等我。；旁白：雨仍在下。；A：已有标签。；说话人待确认，但正文不能变化。')


def test_empty_dialogue_does_not_create_prompt_text() -> None:
    assert compose_shot_prompt({"dialogue": []}) == ""
    assert compose_shot_prompt({"dialogue": [{"speaker": "A", "text": ""}]}) == ""

from __future__ import annotations

import pytest

from local_drama.application.story_pipeline_ai import EPISODE_SCHEMA, FullStoryAIGenerationService
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.story_entities import assess_entity_name


def _episode() -> dict:
    return {
        "title": "重生坠仙谷",
        "summary": "林枫重生后直面宗门冲突。",
        "logline": "林枫带着前世记忆逆转命运。",
        "opening_hook": "林枫在雾中醒来。",
        "core_conflict": "林枫拒绝交出神剑。",
        "ending_hook": "宗门风暴即将爆发。",
        "theme": "重生与选择",
        "source_evidence": ["林枫拒绝交出神剑。"],
        "entity_observations": {
            "characters": [{"name": "林枫", "observation": "本集核心人物"}],
            "scenes": [{"name": "青云宗主殿", "observation": "重复使用的宗门空间"}],
            "props": [{"name": "斩龙神剑", "observation": "推动冲突的关键道具"}],
        },
    }


def test_episode_plan_accepts_lightweight_outline_without_shots() -> None:
    assert FullStoryAIGenerationService._validate_episode(_episode(), 1, 120) is None


def test_episode_plan_rejects_missing_core_conflict() -> None:
    episode = _episode()
    episode["core_conflict"] = ""
    with pytest.raises(DomainRuleError) as caught:
        FullStoryAIGenerationService._validate_episode(episode, 1, 120)
    assert caught.value.code == "PIPELINE_LLM_EPISODE_INCOMPLETE"


def test_episode_plan_requires_entity_observation_lists() -> None:
    episode = _episode()
    episode["entity_observations"]["props"] = None
    with pytest.raises(DomainRuleError) as caught:
        FullStoryAIGenerationService._validate_episode(episode, 1)
    assert caught.value.code == "PIPELINE_LLM_EPISODE_INCOMPLETE"


def test_episode_generation_repairs_incomplete_model_output() -> None:
    class RepairingClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def chat_json(self, _system, prompt, *, json_schema, inference_options):
            self.calls.append({"prompt": prompt, "schema": json_schema, "options": inference_options})
            if len(self.calls) == 1:
                return {"title": "重生坠仙谷", "summary": "林枫苏醒。", "core_conflict": "", "ending_hook": "风暴将至。"}
            return _episode()

    client = RepairingClient()
    service = object.__new__(FullStoryAIGenerationService)
    result = service._generate_episode(
        client,
        "system",
        {"number": 1, "source_text": "林枫在雾中醒来。"},
        "国风仙侠",
        60,
    )

    assert result["core_conflict"] == "林枫拒绝交出神剑。"
    assert len(client.calls) == 2
    assert "校验详情" in client.calls[1]["prompt"]
    assert client.calls[1]["options"]["temperature"] == 0.1


def test_episode_schema_rejects_empty_required_text_during_model_generation() -> None:
    assert EPISODE_SCHEMA["properties"]["title"]["minLength"] == 1
    assert EPISODE_SCHEMA["properties"]["summary"]["minLength"] == 1
    assert EPISODE_SCHEMA["properties"]["core_conflict"]["minLength"] == 1
    assert EPISODE_SCHEMA["properties"]["ending_hook"]["minLength"] == 1


def test_episode_generation_fails_after_bounded_real_model_repairs() -> None:
    class AlwaysIncompleteClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat_json(self, *_args, **_kwargs):
            self.calls += 1
            return {"title": "只有标题"}

    client = AlwaysIncompleteClient()
    service = object.__new__(FullStoryAIGenerationService)
    with pytest.raises(DomainRuleError) as caught:
        service._generate_episode(
            client, "system", {"number": 1, "source_text": "林枫在雾中醒来。"}, "国风仙侠", 60,
        )

    assert client.calls == 3
    assert caught.value.code == "PIPELINE_LLM_EPISODE_INCOMPLETE"


def test_episode_generation_checks_cancellation_before_repair() -> None:
    class IncompleteClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat_json(self, *_args, **_kwargs):
            self.calls += 1
            return {"title": "只有标题"}

    client = IncompleteClient()
    service = object.__new__(FullStoryAIGenerationService)
    with pytest.raises(DomainRuleError) as caught:
        service._generate_episode(
            client,
            "system",
            {"number": 1, "source_text": "原文"},
            "国风仙侠",
            60,
            lambda: True,
        )

    assert caught.value.code == "JOB_CANCELLED"
    assert client.calls == 1


def test_story_synthesis_splits_truncated_output_into_real_model_components() -> None:
    complete = {
        "story_bible": {
            "title": "照骨灯",
            "logline": "凡人执灯照骨。",
            "synopsis": "凡人修行故事。",
            "central_conflict": "凡人与仙途冲突。",
            "world_rules": ["修行有代价"],
            "visual_style": "国风仙侠",
            "continuity_facts": ["照骨灯贯穿全剧"],
        },
        "characters": [{
            "name": "沈砚", "aliases": [], "role": "主角", "introduction": "执灯者",
            "appearance": "青年修士", "visual_prompt": "青年修士，执灯", "importance": "CORE",
        }],
        "scenes": [],
        "props": [],
    }

    class RepairingClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def chat_json(self, _system, prompt, *, json_schema, inference_options):
            self.calls.append({"prompt": prompt, "schema": json_schema, "options": inference_options})
            if len(self.calls) == 1:
                return {"scenes": [], "_normalization": {"source_top_level": "array"}}
            key = ("story_bible", "characters", "scenes", "props")[len(self.calls) - 2]
            return {key: complete[key]}

    client = RepairingClient()
    service = object.__new__(FullStoryAIGenerationService)
    result = service._synthesise(client, "system", [{"number": 1, "summary": "凡人执灯"}], "国风仙侠")

    assert result == complete
    assert len(client.calls) == 5
    assert "顶层只返回 story_bible" in client.calls[1]["prompt"]
    assert "顶层只返回 characters" in client.calls[2]["prompt"]
    assert client.calls[1]["options"]["temperature"] == 0.15


def test_generation_rejects_checkpoints_from_removed_source_fill_fallback() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls = 0

        def chat_json(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return _episode()
            return {
                "story_bible": {
                    "title": "照骨灯",
                    "logline": "凡人执灯照骨。",
                    "synopsis": "凡人修行故事。",
                    "central_conflict": "凡人与仙途冲突。",
                    "world_rules": ["修行有代价"],
                    "visual_style": "国风仙侠",
                    "continuity_facts": ["照骨灯贯穿全剧"],
                },
                "characters": [{
                    "name": "沈砚", "aliases": [], "role": "主角", "introduction": "执灯者",
                    "appearance": "青年修士", "visual_prompt": "青年修士，执灯", "importance": "CORE",
                }],
                "scenes": [],
                "props": [],
            }

    saved = {**_episode(), "number": 1, "normalization_warnings": ["MODEL_OUTPUT_STRUCTURAL_GAPS_FILLED_FROM_SOURCE"]}
    client = Client()
    service = object.__new__(FullStoryAIGenerationService)
    service.resolve_client = lambda _profile: (client, {"profile_version_id": "p1", "provider": "LOCAL", "model": "m1"})
    checkpoints: list[list[dict]] = []

    result = service.generate(
        episode_specs=[{"number": 1, "code": "E001", "source_text": "真实原文"}],
        visual_style="国风仙侠",
        target_seconds=60,
        resume_episodes=[saved],
        on_episode_checkpoint=lambda items, _done, _total: checkpoints.append(items),
    )

    assert client.calls == 2
    assert len(result["episodes"]) == 1
    assert "normalization_warnings" not in result["episodes"][0]
    assert checkpoints and "normalization_warnings" not in checkpoints[0][0]


@pytest.mark.parametrize(
    "name",
    [
        "微笑", "注视着主", "顾长风淡", "顾长风笑", "她仍笑着", "顾长风纠正", "棠在灯里急",
        "人隔着屏风", "屏风后的人", "她咳着血", "仓趴在桌上", "严小叶却", "沈砚立刻",
        "沈砚无法回", "阿鹊烦躁地", "画像", "一道",
    ],
)
def test_character_name_quality_rejects_action_and_state_fragments(name: str) -> None:
    assessment = assess_entity_name("CHARACTER", name)
    assert assessment.valid is False
    assert assessment.reason


@pytest.mark.parametrize("name", ["顾长风", "林枫", "沈知微"])
def test_character_name_quality_keeps_normal_names(name: str) -> None:
    assert assess_entity_name("CHARACTER", name).valid is True

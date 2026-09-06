from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.migrate import migrate

from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database


@pytest.fixture()
def workspace(tmp_path: Path) -> Settings:
    return Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
    )


@pytest.fixture()
def database(workspace: Settings) -> Database:
    workspace.ensure_roots()
    migrate(workspace.database_path)
    return Database(workspace.database_path)


@pytest.fixture()
def mock_story_pipeline_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep pipeline integration tests deterministic without contacting a real model."""
    from local_drama.application.story_pipeline_ai import FullStoryAIGenerationService

    def readiness(self, profile_version_id=None):
        del self
        return {
            "required": True,
            "ready": True,
            "profile_version_id": profile_version_id or "profile-test",
            "provider": "TEST_LLM",
            "model": "test-story-model",
            "base_url": "http://127.0.0.1:1",
        }

    def generate(
        self, *, episode_specs, visual_style, target_seconds, profile_version_id=None,
        cancel_check=None, on_episode=None, resume_episodes=None, on_episode_checkpoint=None,
    ):
        del self, profile_version_id, cancel_check, target_seconds
        episodes = [dict(item) for item in (resume_episodes or [])]
        if episodes and on_episode:
            on_episode(len(episodes), len(episode_specs))
        for index, spec in enumerate(episode_specs[len(episodes):], start=len(episodes) + 1):
            episodes.append({
                "number": spec["number"], "code": spec["code"], "title": spec["title"],
                "summary": spec["summary"], "logline": "主角直面本集危机。", "opening_hook": "危机突现。",
                "core_conflict": "主角与阻力正面碰撞。", "ending_hook": "新的危险逼近。",
                "theme": "选择与成长", "source_evidence": [spec["summary"]],
                "entity_observations": {"characters": [], "scenes": [], "props": []},
                "source_start_paragraph": spec.get("source_start_paragraph"),
                "source_end_paragraph": spec.get("source_end_paragraph"),
            })
            if on_episode_checkpoint:
                on_episode_checkpoint([dict(item) for item in episodes], index, len(episode_specs))
            if on_episode:
                on_episode(index, len(episode_specs))
        characters = [
            {"name": name, "aliases": [], "role": role, "introduction": intro,
             "appearance": "清晰可辨的东方人物外形", "visual_prompt": "东方仙侠人物，全身设定", "importance": "CORE",
             "kind": "CHARACTER", "description": intro}
            for name, role, intro in [
                ("林枫", "主角", "重生后试图逆转命运的核心人物。"),
                ("顾清雪", "重要伙伴", "冷静敏锐、及时示警的重要同伴。"),
                ("楚天极", "宗门长者", "掌握宗门权力与秘密的白发长者。"),
            ]
        ]
        scenes = [
            {"name": name, "introduction": intro, "visual_prompt": "东方仙侠电影场景，无人物",
             "importance": "RECURRING", "kind": "SCENE", "description": intro}
            for name, intro in [("坠仙谷", "雾气弥漫、地势险峻的重生起点。"), ("青云宗主殿", "巍峨庄严、权力秩序鲜明的宗门核心空间。")]
        ]
        props = [
            {"name": name, "introduction": intro, "visual_prompt": "仙侠关键道具产品特写",
             "importance": "CORE", "kind": "PROP", "description": intro}
            for name, intro in [("九阳神丹", "散发微光、影响命运走向的关键丹药。"), ("斩龙神剑", "与宗门冲突紧密相关的关键神剑。")]
        ]
        return {
            "story_bible": {
                "title": "逆命仙途", "logline": "重生少年以旧日记忆逆转命运。", "synopsis": "林枫重生后与顾清雪共同面对追兵及宗门权力。",
                "central_conflict": "林枫改变命运的意志与既有宗门秩序冲突。",
                "world_rules": ["力量需要代价"], "visual_style": visual_style,
                "continuity_facts": ["林枫保留前世记忆"],
            },
            "episodes": episodes,
            "assets": {"characters": characters, "scenes": scenes, "props": props},
            "metadata": {"generation_mode": "LLM_STAGED", "profile_version_id": "profile-test", "provider": "TEST_LLM", "model": "test-story-model", "llm_call_count": len(episodes) + 1, "generated_at": "2026-01-01T00:00:00+00:00", "media_generation_started": False, "coverage": ["EPISODE_PLAN", "STORY_MEMORY", "CORE_VISUAL_ASSETS"]},
            "shot_count": 0,
        }

    monkeypatch.setattr(FullStoryAIGenerationService, "readiness", readiness)
    monkeypatch.setattr(FullStoryAIGenerationService, "generate", generate)

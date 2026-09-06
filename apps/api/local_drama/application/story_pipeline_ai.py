"""Lightweight whole-story analysis for the one-click production pipeline.

The whole-story pass deliberately stops at episode outlines, compact continuity
memory and reusable visual assets. Scene, shot and dialogue detail belongs to
the per-episode production run where it can be generated from the relevant
source range instead of becoming a large, stale dossier up front.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Callable

from local_drama.application.ports.creative_generation import LocalLLMClientProviderPort
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.story_entities import assess_entity_name
from local_drama.infrastructure.local_llm import LocalLLMClient

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": True}


STRING = {"type": "string", "minLength": 1}
STRINGS = {"type": "array", "items": STRING}

OBSERVATIONS_SCHEMA = _object_schema(
    {
        "characters": {"type": "array", "items": _object_schema({"name": STRING, "observation": STRING}, ["name", "observation"])},
        "scenes": {"type": "array", "items": _object_schema({"name": STRING, "observation": STRING}, ["name", "observation"])},
        "props": {"type": "array", "items": _object_schema({"name": STRING, "observation": STRING}, ["name", "observation"])},
    },
    ["characters", "scenes", "props"],
)

EPISODE_SCHEMA = _object_schema(
    {
        "title": STRING,
        "summary": STRING,
        "logline": STRING,
        "opening_hook": STRING,
        "core_conflict": STRING,
        "ending_hook": STRING,
        "theme": STRING,
        "source_evidence": STRINGS,
        "entity_observations": OBSERVATIONS_SCHEMA,
    },
    ["title", "summary", "core_conflict", "ending_hook", "source_evidence", "entity_observations"],
)

CHARACTER_SCHEMA = _object_schema(
    {
        "name": STRING,
        "aliases": STRINGS,
        "role": STRING,
        "introduction": STRING,
        "appearance": STRING,
        "visual_prompt": STRING,
        "importance": {"type": "string", "enum": ["CORE", "RECURRING"]},
    },
    ["name", "role", "introduction", "appearance", "visual_prompt", "importance"],
)

LOCATION_SCHEMA = _object_schema(
    {
        "name": STRING,
        "introduction": STRING,
        "visual_prompt": STRING,
        "importance": {"type": "string", "enum": ["CORE", "RECURRING"]},
    },
    ["name", "introduction", "visual_prompt", "importance"],
)

PROP_SCHEMA = _object_schema(
    {
        "name": STRING,
        "introduction": STRING,
        "visual_prompt": STRING,
        "importance": {"type": "string", "enum": ["CORE", "RECURRING"]},
    },
    ["name", "introduction", "visual_prompt", "importance"],
)

STORY_MEMORY_SCHEMA = _object_schema(
    {
        "title": STRING,
        "logline": STRING,
        "synopsis": STRING,
        "central_conflict": STRING,
        "world_rules": STRINGS,
        "visual_style": STRING,
        "continuity_facts": STRINGS,
    },
    ["title", "logline", "synopsis", "central_conflict", "world_rules", "visual_style", "continuity_facts"],
)

SYNTHESIS_SCHEMA = _object_schema(
    {
        "story_bible": STORY_MEMORY_SCHEMA,
        "characters": {"type": "array", "items": CHARACTER_SCHEMA},
        "scenes": {"type": "array", "items": LOCATION_SCHEMA},
        "props": {"type": "array", "items": PROP_SCHEMA},
    },
    ["story_bible", "characters", "scenes", "props"],
)

SYNTHESIS_COMPONENT_SCHEMAS = {
    "story_bible": _object_schema({"story_bible": STORY_MEMORY_SCHEMA}, ["story_bible"]),
    "characters": _object_schema({"characters": {"type": "array", "items": CHARACTER_SCHEMA}}, ["characters"]),
    "scenes": _object_schema({"scenes": {"type": "array", "items": LOCATION_SCHEMA}}, ["scenes"]),
    "props": _object_schema({"props": {"type": "array", "items": PROP_SCHEMA}}, ["props"]),
}


class FullStoryAIGenerationService:
    """Create only the global plan and reusable production memory."""

    def __init__(
        self,
        database: DatabaseUnitOfWork,
        settings: Settings,
        *,
        llm: LocalLLMClientProviderPort,
    ) -> None:
        self.database = database
        self.settings = settings
        self.llm = llm

    def resolve_client(self, profile_version_id: str | None = None) -> tuple[LocalLLMClient, dict[str, Any]]:
        selected_id = _text(profile_version_id) or None
        if selected_id:
            with self.database.connect() as connection:
                row = connection.execute(
                    "SELECT id,status,capability FROM execution_profile_versions WHERE id=?",
                    (selected_id,),
                ).fetchone()
            if row is None or _text(row["status"]).upper() != "PUBLISHED" or _text(row["capability"]).upper() != "LLM_STORY_PARSE":
                raise DomainRuleError("PIPELINE_LLM_PROFILE_INVALID", "所选故事解析模型方案不存在、未发布或能力不匹配")
        else:
            with self.database.connect() as connection:
                row = connection.execute(
                    """SELECT epv.id FROM execution_profile_versions epv
                    JOIN execution_profiles ep ON ep.id=epv.execution_profile_id
                    WHERE epv.capability='LLM_STORY_PARSE' AND epv.status='PUBLISHED'
                    ORDER BY epv.updated_at DESC,epv.version_no DESC LIMIT 1"""
                ).fetchone()
            selected_id = _text(row["id"]) if row else None
        try:
            client = self.llm.client(profile_version_id=selected_id) if selected_id else self.llm.client()
        except DomainRuleError as error:
            raise DomainRuleError(
                "PIPELINE_LLM_REQUIRED",
                "一键制作需要可用的故事解析模型；请先在“模型与能力”中发布故事解析方案",
                {"cause": error.code},
            ) from error
        return client, {
            "required": True,
            "ready": True,
            "profile_version_id": selected_id,
            "provider": client.provider,
            "model": client.model,
            "base_url": client.base_url,
        }

    def readiness(self, profile_version_id: str | None = None) -> dict[str, Any]:
        try:
            _, info = self.resolve_client(profile_version_id)
            return info
        except DomainRuleError as error:
            return {
                "required": True,
                "ready": False,
                "profile_version_id": _text(profile_version_id) or None,
                "provider": None,
                "model": None,
                "error_code": error.code,
                "message": error.message,
            }

    @staticmethod
    def _episode_prompt(spec: dict[str, Any], visual_style: str, target_seconds: int) -> str:
        source = _text(spec.get("source_text"))[:24_000]
        return (
            f"请为第 {spec['number']} 集生成轻量制作提纲。单集目标约 {target_seconds} 秒；视觉方向：{visual_style}。\n"
            "只输出本集标题、摘要、核心冲突、开场钩子、结尾钩子、主题与原文证据；"
            "不要生成场次、镜头、对白、运镜或生图提示词，这些会在制作本集时按需生成。"
            "实体观察只记录具名、重复出现或影响视觉一致性的核心人物、可复用地点和关键道具。"
            "不得把动作、表情、句子片段、代词或临时路人当作实体名称。"
            "不确定事实保持不确定，不得自行补写人物经历。\n\n"
            f"原稿：\n{source}"
        )

    @staticmethod
    def _validate_episode(value: dict[str, Any], number: int, target_seconds: int | None = None) -> None:
        del target_seconds
        required = ("title", "summary", "core_conflict", "ending_hook")
        missing = [key for key in required if not _text(value.get(key))]
        observations = value.get("entity_observations")
        if missing or not isinstance(observations, dict):
            raise DomainRuleError(
                "PIPELINE_LLM_EPISODE_INCOMPLETE",
                f"大模型返回的第 {number} 集提纲不完整",
                {"missing": missing, "has_entity_observations": isinstance(observations, dict)},
            )
        for key in ("characters", "scenes", "props"):
            if not isinstance(observations.get(key), list):
                raise DomainRuleError(
                    "PIPELINE_LLM_EPISODE_INCOMPLETE",
                    f"大模型返回的第 {number} 集实体观察不完整",
                    {"missing": [key]},
                )

    @staticmethod
    def _compact_digest(item: dict[str, Any]) -> dict[str, Any]:
        observations: dict[str, list[dict[str, str]]] = {}
        raw_observations = item.get("entity_observations")
        for key in ("characters", "scenes", "props"):
            rows = raw_observations.get(key) if isinstance(raw_observations, dict) else []
            observations[key] = [
                {"name": _text(row.get("name"))[:80], "observation": _text(row.get("observation"))[:360]}
                for row in (rows if isinstance(rows, list) else [])[:30]
                if isinstance(row, dict) and _text(row.get("name"))
            ]
        return {
            "number": item["number"],
            "title": _text(item["title"])[:160],
            "summary": _text(item["summary"])[:700],
            "theme": _text(item.get("theme"))[:160],
            "entity_observations": observations,
        }

    @staticmethod
    def _validate_synthesis(value: dict[str, Any]) -> None:
        memory = value.get("story_bible")
        if not isinstance(memory, dict):
            raise DomainRuleError("PIPELINE_LLM_BIBLE_INCOMPLETE", "大模型没有返回全剧创作记忆")
        required_memory = ("title", "logline", "synopsis", "central_conflict", "visual_style")
        missing_memory = [key for key in required_memory if not _text(memory.get(key))]
        if missing_memory or not isinstance(memory.get("world_rules"), list) or not isinstance(memory.get("continuity_facts"), list):
            raise DomainRuleError(
                "PIPELINE_LLM_BIBLE_INCOMPLETE",
                "大模型返回的全剧创作记忆不完整",
                {"missing": missing_memory},
            )
        for key, kind, required in (
            ("characters", "CHARACTER", ("name", "role", "introduction", "appearance", "visual_prompt")),
            ("scenes", "SCENE", ("name", "introduction", "visual_prompt")),
            ("props", "PROP", ("name", "introduction", "visual_prompt")),
        ):
            rows = value.get(key)
            if not isinstance(rows, list):
                raise DomainRuleError("PIPELINE_LLM_ASSETS_INCOMPLETE", f"大模型没有返回 {key} 列表")
            for item in rows:
                missing = [field for field in required if not isinstance(item, dict) or not _text(item.get(field))]
                if missing:
                    raise DomainRuleError(
                        "PIPELINE_LLM_ASSET_INCOMPLETE",
                        "核心资产信息不完整",
                        {"kind": kind, "name": item.get("name") if isinstance(item, dict) else None, "missing": missing},
                    )
                assessment = assess_entity_name(kind, item.get("name"))
                if not assessment.valid:
                    raise DomainRuleError(
                        "PIPELINE_LLM_ASSET_NAME_INVALID",
                        "资产名称不是可复用实体",
                        {"kind": kind, "name": item.get("name"), "reason": assessment.reason},
                    )
        if not value["characters"]:
            raise DomainRuleError("PIPELINE_LLM_CHARACTERS_REQUIRED", "大模型未识别出核心人物")

    def _synthesise(self, client: LocalLLMClient, system: str, payload: Any, visual_style: str) -> dict[str, Any]:
        prompt = (
            f"请根据逐集提纲建立精简的全剧创作记忆和可复用视觉资产。统一视觉方向：{visual_style}。\n"
            "创作记忆只保留故事概述、核心冲突、世界规则和必须跨集保持一致的事实。"
            "人物只保留主角、重要配角和重复出场角色，最多 24 人；"
            "场景只保留重复使用或视觉重要的地点，最多 24 个；道具只保留推动剧情或反复出现的物件，最多 16 个。"
            "每项资产只写一段简短介绍和一个可直接用于生成主参考图的 visual_prompt。"
            "一次性路人、普通家具、动作、表情和对白片段不要建档；同一实体的别名必须合并。\n\n输入：\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        result = client.chat_json(
            system,
            prompt,
            json_schema=SYNTHESIS_SCHEMA,
            inference_options={"temperature": 0.2, "top_p": 0.9, "max_tokens": 6000, "num_ctx": 49152},
        )
        try:
            self._validate_synthesis(result)
            return result
        except DomainRuleError as error:
            logger.warning(
                "splitting incomplete story synthesis into model-generated components: "
                f"code={error.code} details={error.details} result_type={type(result).__name__} "
                f"result_keys={sorted(result.keys()) if isinstance(result, dict) else []}",
            )

        # Large local-model responses can be truncated before the outer JSON
        # closes.  Generate each contract independently instead of filling any
        # field in application code; every value still comes from the selected
        # model and the complete set is validated before it can be persisted.
        component_instructions = {
            "story_bible": (
                "只生成全剧创作记忆。顶层只返回 story_bible；必须包含 title、logline、synopsis、"
                "central_conflict、world_rules、visual_style、continuity_facts。"
            ),
            "characters": (
                "只生成人物资产。顶层只返回 characters；仅保留主角、重要配角和重复出场角色，最多 24 人。"
            ),
            "scenes": (
                "只生成场景资产。顶层只返回 scenes；仅保留重复使用或视觉重要的地点，最多 24 个。"
            ),
            "props": (
                "只生成道具资产。顶层只返回 props；仅保留推动剧情或反复出现的物件，最多 16 个。"
            ),
        }
        source_payload = json.dumps(payload, ensure_ascii=False)
        merged: dict[str, Any] = {}
        for key in ("story_bible", "characters", "scenes", "props"):
            component_prompt = (
                f"{component_instructions[key]}统一视觉方向：{visual_style}。"
                "只能依据输入的逐集提纲，不得杜撰输入中不存在的事实。"
                "介绍和视觉提示词保持精炼；一次性路人、动作、表情、对白片段不得建档。\n\n"
                f"逐集提纲：\n{source_payload}"
            )
            component = client.chat_json(
                system,
                component_prompt,
                json_schema=SYNTHESIS_COMPONENT_SCHEMAS[key],
                inference_options={"temperature": 0.15, "top_p": 0.85, "max_tokens": 3600, "num_ctx": 49152},
            )
            if not isinstance(component, dict) or key not in component:
                raise DomainRuleError(
                    "PIPELINE_LLM_SYNTHESIS_COMPONENT_INCOMPLETE",
                    f"大模型没有返回全剧综合字段 {key}",
                    {"component": key, "result_keys": sorted(component.keys()) if isinstance(component, dict) else []},
                )
            merged[key] = component[key]

        self._validate_synthesis(merged)
        return merged

    def _generate_episode(
        self,
        client: LocalLLMClient,
        system: str,
        spec: dict[str, Any],
        visual_style: str,
        target_seconds: int,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Generate one outline and repair schema-incomplete model output in place.

        JSON-schema constrained local models can still return empty strings or
        omit nested observation arrays.  Treat that as recoverable model output,
        not as a terminal pipeline failure: provide the invalid object and exact
        validation details to a bounded repair pass.  Transport/profile errors
        remain terminal and are never hidden by this recovery path.
        """

        prompt = self._episode_prompt(spec, visual_style, target_seconds)
        result = client.chat_json(
            system,
            prompt,
            json_schema=EPISODE_SCHEMA,
            inference_options={"temperature": 0.2, "top_p": 0.9, "max_tokens": 1800, "num_ctx": 32768},
        )
        for repair_no in range(3):
            try:
                self._validate_episode(result, int(spec["number"]))
                return result
            except DomainRuleError as error:
                if error.code != "PIPELINE_LLM_EPISODE_INCOMPLETE":
                    raise
                if repair_no >= 2:
                    raise
                if cancel_check and cancel_check():
                    raise DomainRuleError("JOB_CANCELLED", "故事分析已取消")
                repair_prompt = (
                    f"第 {spec['number']} 集提纲未通过结构校验。请修复下列 JSON，保留已有有效内容，"
                    "补齐所有必填文本；entity_observations 必须包含 characters、scenes、props 三个数组，"
                    "没有可靠实体时返回空数组。只返回完整 JSON 对象。\n"
                    f"校验详情：{json.dumps(error.details or {}, ensure_ascii=False)}\n"
                    f"待修复对象：{json.dumps(result, ensure_ascii=False)}\n\n"
                    f"原始要求：\n{prompt}"
                )
                logger.warning(
                    "repairing incomplete episode outline: "
                    f"episode={spec['number']} attempt={repair_no + 1} details={error.details} "
                    f"result_type={type(result).__name__} "
                    f"result_keys={sorted(result.keys()) if isinstance(result, dict) else []}",
                )
                result = client.chat_json(
                    system,
                    repair_prompt,
                    json_schema=EPISODE_SCHEMA,
                    inference_options={"temperature": 0.1, "top_p": 0.85, "max_tokens": 2400, "num_ctx": 32768},
                )

        raise DomainRuleError("PIPELINE_LLM_EPISODE_INCOMPLETE", f"大模型返回的第 {spec['number']} 集提纲不完整")

    def generate(
        self,
        *,
        episode_specs: list[dict[str, Any]],
        visual_style: str,
        target_seconds: int,
        profile_version_id: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
        on_episode: Callable[[int, int], None] | None = None,
        resume_episodes: list[dict[str, Any]] | None = None,
        on_episode_checkpoint: Callable[[list[dict[str, Any]], int, int], None] | None = None,
    ) -> dict[str, Any]:
        client, model_info = self.resolve_client(profile_version_id)
        system = (
            "你是 AI 漫剧的故事规划 Agent。只输出符合 JSON Schema 的中文对象。"
            "全剧阶段只做轻量规划，不提前生成逐镜制作细节，也不编造原稿没有的事实。"
        )
        episodes: list[dict[str, Any]] = []
        for spec, saved in zip(episode_specs, resume_episodes or []):
            if not isinstance(saved, dict) or int(saved.get("number") or 0) != int(spec["number"]):
                break
            # Checkpoints created by the removed source-fill fallback are not
            # model output and must never be accepted as production evidence.
            if saved.get("normalization_warnings"):
                break
            try:
                self._validate_episode(saved, int(spec["number"]))
            except DomainRuleError:
                break
            episodes.append(dict(saved))
        if episodes and on_episode:
            on_episode(len(episodes), len(episode_specs))
        for index, spec in enumerate(episode_specs[len(episodes):], start=len(episodes) + 1):
            if cancel_check and cancel_check():
                raise DomainRuleError("JOB_CANCELLED", "故事分析已取消")
            try:
                result = self._generate_episode(client, system, spec, visual_style, target_seconds, cancel_check)
            except DomainRuleError:
                raise
            except Exception as error:
                raise DomainRuleError("PIPELINE_LLM_GENERATION_FAILED", f"大模型分析第 {spec['number']} 集失败") from error
            result.update(
                {
                    "number": int(spec["number"]),
                    "code": _text(spec["code"]),
                    "source_start_paragraph": spec.get("source_start_paragraph"),
                    "source_end_paragraph": spec.get("source_end_paragraph"),
                }
            )
            episodes.append(result)
            if on_episode_checkpoint:
                on_episode_checkpoint([dict(item) for item in episodes], index, len(episode_specs))
            if on_episode:
                on_episode(index, len(episode_specs))

        try:
            synthesis = self._synthesise(client, system, [self._compact_digest(item) for item in episodes], visual_style)
        except DomainRuleError:
            raise
        except Exception as error:
            raise DomainRuleError("PIPELINE_LLM_GENERATION_FAILED", "大模型合并全剧规划失败") from error

        limits = {"characters": 24, "scenes": 24, "props": 16}
        for key, kind in (("characters", "CHARACTER"), ("scenes", "SCENE"), ("props", "PROP")):
            clean: list[dict[str, Any]] = []
            seen: set[str] = set()
            for item in synthesis[key]:
                name = _text(item.get("name"))
                identity = name.casefold()
                if not name or identity in seen:
                    continue
                seen.add(identity)
                item["name"] = name
                item["kind"] = kind
                item["description"] = _text(item.get("introduction"))
                clean.append(item)
                if len(clean) >= limits[key]:
                    break
            synthesis[key] = clean

        return {
            "story_bible": synthesis["story_bible"],
            "episodes": episodes,
            "assets": {key: synthesis[key] for key in ("characters", "scenes", "props")},
            "metadata": {
                "generation_mode": "LLM_STAGED",
                "profile_version_id": model_info["profile_version_id"],
                "provider": model_info["provider"],
                "model": model_info["model"],
                "llm_call_count": len(episodes) + 1,
                "generated_at": _now(),
                "media_generation_started": False,
                "coverage": ["EPISODE_PLAN", "STORY_MEMORY", "CORE_VISUAL_ASSETS"],
                "deferred_to_episode_run": ["SCENE_BREAKDOWN", "SHOT_TEXT", "DIALOGUE_TEXT", "SHOT_PROMPTS"],
            },
            "shot_count": 0,
        }

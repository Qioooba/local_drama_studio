"""Explainer AI story breakdown service.

Invokes local LLM (Qwen3.8-27B / configured LLM_STORY_PARSE model) to disassemble
raw story text into an explainer script revision with chapters, segments,
screen subtitle text (display_text), oral broadcast text (spoken_text), and
pronunciation maps.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Sequence

from local_drama.application.explainers.narration import ExplainerNarrationService
from local_drama.application.explainers.text_planner import (
    SEGMENT_SCHEMA,
    _normalise_spoken_text,
)
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.explainers.contracts import (
    ExplainerContractError,
    ExplainerErrorCode,
    StatementType,
)
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

logger = logging.getLogger(__name__)

BREAKDOWN_SYSTEM_PROMPT = (
    "你是专业的影视解说与故事精拆制作助手。你的任务是将用户提供的真实故事或原稿，"
    "拆解为专业解说视频制作所需要的完整分段讲稿（包含口播稿和字幕）。只输出合法的 JSON 对象。"
)


class ExplainerStoryBreakdownService:
    def __init__(self, database: DatabaseUnitOfWork, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def _resolve_client(self, profile_version_id: str | None = None) -> tuple[Any, str | None]:
        llm_service = LocalLLMService(self.database, self.settings)
        if profile_version_id and profile_version_id.strip():
            client = llm_service.client(profile_version_id=profile_version_id.strip())
            return client, profile_version_id.strip()

        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT epv.id, epv.model_bundle_json FROM execution_profile_versions epv
                JOIN execution_profiles ep ON ep.id = epv.execution_profile_id
                WHERE epv.capability = 'LLM_STORY_PARSE' AND epv.status = 'PUBLISHED'
                ORDER BY epv.updated_at DESC, epv.version_no DESC"""
            ).fetchall()
            for r in rows:
                bundle = str(r["model_bundle_json"] or "").lower()
                if "qwen" in bundle and "27b" in bundle:
                    selected_id = str(r["id"])
                    return llm_service.client(profile_version_id=selected_id), selected_id
            if rows:
                selected_id = str(rows[0]["id"])
                return llm_service.client(profile_version_id=selected_id), selected_id

        # Fallback to default client
        return llm_service.client(), None

    def breakdown_story(
        self,
        project_id: str,
        payload: Any,
    ) -> dict[str, Any]:
        story_text = str(payload.story_text or "").strip()
        if len(story_text) < 10:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "故事原文长度至少需要 10 个字符", {"characters": len(story_text)}
            )

        client, used_profile_id = self._resolve_client(payload.profile_version_id)

        with self.database.connect() as connection:
            repo = ExplainerRepository(connection)
            repo.require_explainer_project(project_id)
            video = repo.require_video_for_project(project_id)

        target_seconds = int(payload.target_seconds or video.get("target_seconds") or 300)
        title = str(payload.title or video.get("title") or "解说作品").strip()
        style = str(payload.style or "深度影视解说与真实故事还原").strip()

        est_chars = max(180, min(int(target_seconds * 3.5), 2500))
        target_segments = max(4, min(int(est_chars / 50), 20))

        user_prompt = (
            f"请将以下故事内容拆解为解说视频工程讲稿：\n\n"
            f"【故事原文】：\n{story_text}\n\n"
            f"【作品标题】：{title}\n"
            f"【目标成片时长】：约 {target_seconds} 秒\n"
            f"【解说风格】：{style}\n\n"
            "【输出结构与字段要求】：\n"
            "必须输出纯 JSON 对象，包含 'outline' 和 'segments' 两大字段：\n"
            "1. 'outline': 章节大纲字符串数组（3 到 5 个核心章节，例如 ['第一幕：密室空床与假人头', '第二幕：六个月前的越狱密谋', '第三幕：冰冷湾流与谜团']）。\n"
            f"2. 'segments': 拆解后的叙述句段列表，总数控制在约 {target_segments} 个句段左右（每段 25 到 60 字），包含以下属性：\n"
            "   - 'canonical_segment_id': 唯一连续编号，从 'seg_001' 起递增。\n"
            "   - 'display_text': 屏幕字幕展示文本。保留规范阿拉伯数字（如“1962年6月12日”、“3名囚犯”）、书名号和通用标点。\n"
            "   - 'spoken_text': 配音口播朗读文本。必须将所有数字转换为中文汉字读音（例如“一九六二年六月十二日”、“三名囚犯”），并去除冒号、斜杠等不便朗读的标点。\n"
            "   - 'statement_type': 取值必须为以下之一：'FACT'（事实陈述）、'ORIGINAL_EXPLANATION'（原理解释）、'TRANSITION'（过渡承接）、'QUESTION'（设问悬念）。\n"
            "   - 'pronunciation_map': 数组，包含 display_text 与 spoken_text 之间的关键替换映射（例如 [{'display': '1962', 'spoken': '一九六二'}, {'display': '6', 'spoken': '六'}]）。如无可为空数组。\n"
            "   - 'pause_after_ms': 播报后的停顿毫秒数（通常 200 到 400）。\n"
            "   - 'chapter_code': 所属章节的简述或标识。\n"
            "3. 节奏要求：紧扣故事原文，节奏清晰，避免冗长或重复。"
        )

        try:
            raw_response = client.chat_json(
                BREAKDOWN_SYSTEM_PROMPT,
                user_prompt,
                json_schema=SEGMENT_SCHEMA,
                inference_options={
                    "temperature": 0.2,
                    "top_p": 0.9,
                    "max_tokens": 4096,
                    "timeout_seconds": 1200.0,
                    "num_ctx": 32768,
                },
            )
        except DomainRuleError as error:
            raise ExplainerContractError(
                ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value,
                f"本地大模型（{client.model}）拆解失败: {error.message}",
                {"cause": error.code},
            ) from error

        if not isinstance(raw_response, Mapping):
            raise ExplainerContractError("SCHEMA_INVALID", "文本模型未返回有效 JSON 对象")

        outline = [str(item) for item in (raw_response.get("outline") or [])]
        raw_segments = raw_response.get("segments") or []
        if not raw_segments:
            raise ExplainerContractError("SCHEMA_INVALID", "大模型未拆解出任何播音句段")

        valid_statement_types = {item.value for item in StatementType}
        processed_segments: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for idx, raw in enumerate(raw_segments, start=1):
            if not isinstance(raw, Mapping):
                continue
            seg_id = str(raw.get("canonical_segment_id") or f"seg_{idx:03d}").strip()
            if seg_id in seen_ids:
                seg_id = f"seg_{idx:03d}"
            seen_ids.add(seg_id)

            display_text = str(raw.get("display_text") or "").strip()
            if not display_text:
                continue

            raw_spoken = str(raw.get("spoken_text") or "").strip() or display_text
            raw_map = [
                {"display": str(p.get("display") or ""), "spoken": str(p.get("spoken") or "")}
                for p in (raw.get("pronunciation_map") or [])
                if str(p.get("display") or "") and str(p.get("spoken") or "")
            ]

            spoken_text, pronunciation_map, norm_display, _disposition = _normalise_spoken_text(
                display_text, raw_spoken, raw_map
            )

            st_type = str(raw.get("statement_type") or StatementType.ORIGINAL_EXPLANATION.value).upper()
            if st_type not in valid_statement_types:
                st_type = StatementType.ORIGINAL_EXPLANATION.value

            pause_after = max(0, min(int(raw.get("pause_after_ms") or 250), 3000))
            chapter_code = str(raw.get("chapter_code") or "").strip() or None

            processed_segments.append(
                {
                    "canonical_segment_id": seg_id,
                    "display_text": norm_display,
                    "spoken_text": spoken_text,
                    "statement_type": st_type,
                    "claim_ids": [],
                    "pronunciation_map": pronunciation_map,
                    "pause_after_ms": pause_after,
                    "chapter_code": chapter_code,
                }
            )

        if not processed_segments:
            raise ExplainerContractError("SCHEMA_INVALID", "拆解后没有有效句段")

        built_chapters: list[dict[str, Any]] = []
        for idx, ot in enumerate(outline, start=1):
            ch_code = f"ch_{idx:02d}"
            built_chapters.append({
                "code": ch_code,
                "title": str(ot),
                "audience_question": "",
                "summary": str(ot),
            })

        if built_chapters:
            seg_per_ch = max(1, len(processed_segments) // len(built_chapters))
            for i, seg in enumerate(processed_segments):
                if not seg.get("chapter_code"):
                    ch_idx = min(i // seg_per_ch, len(built_chapters) - 1)
                    seg["chapter_code"] = built_chapters[ch_idx]["code"]

        with self.database.transaction() as connection:
            repo = ExplainerRepository(connection)
            service = ExplainerNarrationService(repo)
            created = service.create_script_revision(
                project_id=project_id,
                video_id=str(video["id"]),
                locale=str(video.get("source_locale") or "zh-CN"),
                title=f"{title} - 智能拆解稿",
                outline=outline,
                chapters=built_chapters if built_chapters else None,
                segments=processed_segments,
                status="DRAFT",
            )
            revision = created["script_revision"]

            # Store the pasted text in explainer_videos input_payload
            try:
                current_input = json.loads(str(video.get("input_payload_json") or "{}"))
            except Exception:
                current_input = {}
            if isinstance(current_input, dict):
                current_input["pasted_text"] = story_text
                current_input["input_kind"] = "PASTED_SCRIPT"
                repo.update(
                    "explainer_videos",
                    str(video["id"]),
                    {
                        "input_payload_json": current_input,
                        "current_script_revision_id": str(revision["id"]),
                    },
                )

        return {
            "status": "PASS",
            "script_revision": revision,
            "outline": outline,
            "segments": created.get("segments", []),
            "segment_count": len(processed_segments),
            "character_count": sum(len(s["display_text"]) for s in processed_segments),
            "model_used": client.model,
            "provider": client.provider,
            "profile_version_id": used_profile_id,
        }

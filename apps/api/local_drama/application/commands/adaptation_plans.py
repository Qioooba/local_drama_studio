"""Commands for creating immutable AdaptationPlan revisions."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from local_drama.application.ports.adaptation_planning import AdaptationPlanningRepository
from local_drama.domain.adaptation_planning import ADAPTATION_MODES, SEASON_STRATEGIES, analysis_manifest_nodes, recommendation
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.source_text import looks_like_source_heading, source_chapters, source_paragraphs


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _positive_int(value: object, *, field: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise DomainRuleError(
            "ADAPTATION_PLAN_CONSTRAINT_INVALID",
            f"{field} 必须在 {minimum}–{maximum} 之间",
            {"field": field},
        )
    return value


class AdaptationPlanCommandService:
    def __init__(self, repository: AdaptationPlanningRepository) -> None:
        self.repository = repository

    def preflight(
        self,
        *,
        project_id: str,
        source_document_version_id: str,
        source_paragraph_start: int | None,
        source_paragraph_end: int | None,
        target_duration_ms: int,
    ) -> dict[str, Any]:
        target_duration_ms = _positive_int(
            target_duration_ms,
            field="target_duration_ms",
            minimum=10_000,
            maximum=3_600_000,
        )
        snapshot = self.repository.source_snapshot(
            project_id=project_id,
            source_document_version_id=source_document_version_id,
        )
        records = source_paragraphs(snapshot.text)
        if not records:
            raise DomainRuleError("ADAPTATION_SOURCE_EMPTY", "原稿没有可规划的正文段落")
        if (source_paragraph_start is None) != (source_paragraph_end is None):
            raise DomainRuleError("ADAPTATION_SCOPE_INCOMPLETE", "原稿范围必须同时提供起始段和结束段")
        start = source_paragraph_start if source_paragraph_start is not None else 1
        end = source_paragraph_end if source_paragraph_end is not None else len(records)
        if start < 1 or end < start or end > len(records):
            raise DomainRuleError(
                "ADAPTATION_SCOPE_INVALID",
                f"原稿范围必须位于 1–{len(records)} 段内，且结束段不得早于起始段",
                {"total_paragraph_count": len(records)},
            )
        selected = records[start - 1 : end]
        body_records = [item for item in selected if not looks_like_source_heading(item.text)]
        selected_text = "\n\n".join(item.text for item in body_records)
        character_count = len(selected_text)
        chapter_items = [
            chapter
            for chapter in source_chapters(records)
            if int(chapter["end_paragraph"]) >= start and int(chapter["start_paragraph"]) <= end
        ]
        paragraph_count = len(selected)
        chapter_count = len(chapter_items)
        mode = recommendation(
            character_count=character_count,
            paragraph_count=paragraph_count,
            chapter_count=chapter_count,
        )
        duration_factor = target_duration_ms / 120_000
        characters_per_episode_high = max(80, int(320 * duration_factor))
        characters_per_episode_low = max(60, int(275 * duration_factor))
        estimate_min = max(1, (character_count + characters_per_episode_high - 1) // characters_per_episode_high)
        estimate_max = max(estimate_min, (character_count + characters_per_episode_low - 1) // characters_per_episode_low)
        diagnosis_type = (
            "NOVEL_LONG_FORM"
            if mode == "COMPLETE_WORK"
            else "NOVEL_EXCERPT"
            if mode == "SERIAL_INCREMENTAL"
            else "SINGLE_EPISODE_SCRIPT"
        )
        scope = {
            "source_document_version_id": snapshot.source_document_version_id,
            "source_paragraph_start": start,
            "source_paragraph_end": end,
            "unicode_start": selected[0].start,
            "unicode_end": selected[-1].end,
            "selection_mode": "EXPLICIT" if source_paragraph_start is not None else "FULL_DOCUMENT",
        }
        warnings: list[dict[str, str]] = []
        if character_count == 0:
            warnings.append({"code": "ADAPTATION_BODY_EMPTY", "message": "所选范围只包含标题，无法生成改编规划"})
        if mode != "SINGLE_EPISODE":
            warnings.append(
                {
                    "code": "ADAPTATION_REVIEW_REQUIRED",
                    "message": "长篇规划只生成待审核草稿，不会创建季度、分集、镜头或覆盖既有内容",
                }
            )
        return {
            "source": {
                "source_document_id": snapshot.source_document_id,
                "source_document_version_id": snapshot.source_document_version_id,
                "title": snapshot.title,
                "source_name": snapshot.source_name,
                "source_sha256": snapshot.source_sha256,
                "text_sha256": snapshot.text_sha256,
            },
            "scope": scope,
            "diagnosis": {
                "type": diagnosis_type,
                "character_count": character_count,
                "paragraph_count": paragraph_count,
                "body_paragraph_count": len(body_records),
                "chapter_count": chapter_count,
                "chapter_titles": [str(chapter["title"]) for chapter in chapter_items[:100]],
                "recommended_mode": mode,
                "estimated_episode_range": {"minimum": estimate_min, "maximum": estimate_max},
                "target_duration_ms": target_duration_ms,
            },
            "execution": {
                "stage": "PREFLIGHT",
                "requires_episode": False,
                "creates_media": False,
                "remote_outbound_consent_required": False,
                "profile_status": "NOT_SELECTED",
                "estimated_input_tokens": max(1, (character_count + 1) // 2),
            },
            "warnings": warnings,
        }

    def create(
        self,
        *,
        project_id: str,
        source_document_version_id: str,
        mode: str,
        source_paragraph_start: int | None,
        source_paragraph_end: int | None,
        target_duration_ms: int,
        episode_strategy: str,
        requested_episode_count: int | None,
        season_strategy: str,
        requested_season_count: int | None,
        profile_version_id: str | None,
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        normalized_mode = mode.strip().upper()
        if normalized_mode not in ADAPTATION_MODES:
            raise DomainRuleError("ADAPTATION_PLAN_MODE_INVALID", "不支持的改编规划模式")
        if not idempotency_key.strip():
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "创建改编规划需要 Idempotency-Key")
        normalized_episode_strategy = episode_strategy.strip().upper()
        if normalized_episode_strategy not in {"AI_ESTIMATE", "FIXED_COUNT"}:
            raise DomainRuleError("ADAPTATION_EPISODE_STRATEGY_INVALID", "集数策略必须是 AI_ESTIMATE 或 FIXED_COUNT")
        if normalized_episode_strategy == "FIXED_COUNT":
            requested_episode_count = _positive_int(
                requested_episode_count,
                field="requested_episode_count",
                minimum=1,
                maximum=2_000,
            )
        elif requested_episode_count is not None:
            raise DomainRuleError("ADAPTATION_EPISODE_STRATEGY_INVALID", "AI 估算模式不能指定集数")
        normalized_season_strategy = season_strategy.strip().upper()
        if normalized_season_strategy not in SEASON_STRATEGIES:
            raise DomainRuleError("ADAPTATION_SEASON_STRATEGY_INVALID", "不支持的季策略")
        if normalized_season_strategy == "FIXED_COUNT":
            requested_season_count = _positive_int(
                requested_season_count,
                field="requested_season_count",
                minimum=1,
                maximum=100,
            )
        elif requested_season_count is not None:
            raise DomainRuleError("ADAPTATION_SEASON_STRATEGY_INVALID", "当前季策略不能指定季数")

        preflight = self.preflight(
            project_id=project_id,
            source_document_version_id=source_document_version_id,
            source_paragraph_start=source_paragraph_start,
            source_paragraph_end=source_paragraph_end,
            target_duration_ms=target_duration_ms,
        )
        if preflight["diagnosis"]["body_paragraph_count"] == 0:
            raise DomainRuleError("ADAPTATION_BODY_EMPTY", "所选范围没有正文，不能创建规划")
        constraints = {
            "target_duration_ms": target_duration_ms,
            "episode_strategy": normalized_episode_strategy,
            "requested_episode_count": requested_episode_count,
            "season_strategy": normalized_season_strategy,
            "requested_season_count": requested_season_count,
        }
        request_fingerprint = _hash(
            {
                "project_id": project_id,
                "source_document_version_id": source_document_version_id,
                "mode": normalized_mode,
                "scope": preflight["scope"],
                "constraints": constraints,
                "profile_version_id": profile_version_id,
                "idempotency_key": idempotency_key,
            }
        )
        existing = self.repository.find_plan_for_request(
            project_id=project_id,
            request_fingerprint=request_fingerprint,
        )
        if existing is not None:
            return {**existing, "idempotent": True}

        snapshot = self.repository.source_snapshot(
            project_id=project_id,
            source_document_version_id=source_document_version_id,
        )
        records = source_paragraphs(snapshot.text)
        chapters = source_chapters(records)
        heading_ordinals = {
            number
            for chapter in chapters
            for number in (int(chapter["start_paragraph"]),)
            if 1 <= number <= len(records) and looks_like_source_heading(records[number - 1].text)
        }
        source_units = [
            {
                "ordinal": index,
                "unit_kind": "HEADING" if index in heading_ordinals else "BODY",
                "source_start": record.start,
                "source_end": record.end,
                "text_sha256": hashlib.sha256(record.text.encode("utf-8")).hexdigest(),
                "chapter_ordinal": next(
                    (
                        chapter_index
                        for chapter_index, chapter in enumerate(chapters, start=1)
                        if int(chapter["start_paragraph"]) <= index <= int(chapter["end_paragraph"])
                    ),
                    None,
                ),
            }
            for index, record in enumerate(records, start=1)
        ]
        plan_id = str(uuid.uuid4())
        revision_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        revision_payload = {
            "source_scope": preflight["scope"],
            "constraints": constraints,
            "diagnosis": preflight["diagnosis"],
            "analysis_contract_version": "adaptation-analysis/v1",
            "prompt_schema_version": "adaptation-plan/v1",
            "profile_version_id": profile_version_id,
            "runtime_contract": {"profile_status": "NOT_SELECTED"},
            "validation_summary": {"state": "NOT_RUN", "issues": []},
        }
        revision_payload["content_sha256"] = _hash(
            {
                "source_text_sha256": snapshot.text_sha256,
                "mode": normalized_mode,
                **revision_payload,
            }
        )
        return self.repository.create_plan(
            plan={
                "id": plan_id,
                "project_id": project_id,
                "source_document_version_id": source_document_version_id,
                "mode": normalized_mode,
            },
            revision={"id": revision_id, **revision_payload},
            run={
                "id": run_id,
                "request_fingerprint": request_fingerprint,
                "estimated_input_tokens": preflight["execution"]["estimated_input_tokens"],
                "estimated_cost_microunits": None,
            },
            source_units=source_units,
            actor=actor,
        )

    def prepare_analysis_manifest(self, *, plan_id: str, actor: str = "local-user") -> dict[str, Any]:
        """Persist the full Map/Reduce graph before any model call is possible."""
        context = self.repository.planning_context(plan_id=plan_id)
        scope = context["source_scope"]
        try:
            source_paragraph_start = int(scope["source_paragraph_start"])
            source_paragraph_end = int(scope["source_paragraph_end"])
        except (KeyError, TypeError, ValueError) as error:
            raise DomainRuleError("ADAPTATION_SCOPE_INVALID", "改编规划缺少有效的冻结原稿范围") from error
        snapshot = self.repository.source_snapshot(
            project_id=str(context["project_id"]),
            source_document_version_id=str(context["source_document_version_id"]),
        )
        body_records = [record for record in source_paragraphs(snapshot.text) if not looks_like_source_heading(record.text)]
        nodes = analysis_manifest_nodes(
            records=body_records,
            source_paragraph_start=source_paragraph_start,
            source_paragraph_end=source_paragraph_end,
            source_text_sha256=snapshot.text_sha256,
        )
        if not nodes:
            raise DomainRuleError("ADAPTATION_BODY_EMPTY", "冻结范围不包含可供分析的正文段落")
        return self.repository.save_analysis_manifest(
            plan_id=plan_id,
            revision_id=str(context["revision_id"]),
            run_id=str(context["run_id"]),
            nodes=nodes,
            actor=actor,
        )

    def analysis_readiness(self, *, plan_id: str, profile_version_id: str) -> dict[str, Any]:
        if not profile_version_id.strip():
            raise DomainRuleError("LOCAL_LLM_PROFILE_REQUIRED", "请先选择一个文本规划 Profile")
        return self.repository.analysis_readiness(
            plan_id=plan_id,
            profile_version_id=profile_version_id,
        )

    def approve(self, *, plan_id: str, expected_content_sha256: str, actor: str = "local-user") -> dict[str, Any]:
        if not expected_content_sha256.strip():
            raise DomainRuleError("ADAPTATION_REVIEW_TOKEN_REQUIRED", "批准改编规划需要当前修订指纹")
        return self.repository.approve_plan(
            plan_id=plan_id,
            expected_content_sha256=expected_content_sha256,
            actor=actor,
        )

    def materialization_preflight(self, *, plan_id: str) -> dict[str, Any]:
        return self.repository.materialization_preflight(plan_id=plan_id)

    def materialize(
        self, *, plan_id: str, expected_content_sha256: str, idempotency_key: str, actor: str = "local-user"
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "发布真实项目结构需要 Idempotency-Key")
        if not expected_content_sha256.strip():
            raise DomainRuleError("ADAPTATION_REVIEW_TOKEN_REQUIRED", "发布真实项目结构需要当前修订指纹")
        return self.repository.materialize_plan(
            plan_id=plan_id,
            expected_content_sha256=expected_content_sha256,
            idempotency_key=idempotency_key,
            actor=actor,
        )

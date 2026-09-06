"""Immutable, auditable human revisions of local-LLM breakdown drafts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any, Mapping

from local_drama.application.breakdown_contracts import validated_breakdown_shot_duration
from local_drama.application.local_llm import (
    validate_scene_dialogue_grounding,
    validate_scene_distinctness,
    validate_scene_source_grounding,
)
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def load_effective_breakdown_draft(
    connection: sqlite3.Connection,
    draft: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return the latest human snapshot, or the immutable model output."""
    revision = connection.execute(
        """SELECT * FROM script_breakdown_draft_revisions
        WHERE breakdown_draft_id=? ORDER BY revision_no DESC,id DESC LIMIT 1""",
        (draft["id"],),
    ).fetchone()
    raw = revision["draft_json"] if revision is not None else draft["draft_json"]
    payload = json.loads(str(raw))
    if not isinstance(payload, dict):
        raise DomainRuleError("BREAKDOWN_DRAFT_NOT_READY", "剧本拆解草稿格式无效")
    return payload, dict(revision) if revision is not None else None


class BreakdownRevisionService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def revise_scene(
        self,
        draft_id: str,
        scene_no: int,
        scene: dict[str, Any],
        *,
        expected_revision: int,
        change_note: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        with self.database.transaction() as connection:
            draft = connection.execute("SELECT * FROM script_breakdown_drafts WHERE id=?", (draft_id,)).fetchone()
            if draft is None:
                raise DomainRuleError("BREAKDOWN_DRAFT_NOT_READY", "剧本拆解草稿不存在")
            if draft["status"] != "DRAFT_READY":
                raise DomainRuleError("BREAKDOWN_DRAFT_NOT_EDITABLE", "只有尚未全部应用的草稿可以修订")
            if int(draft["revision"]) != expected_revision:
                raise DomainRuleError(
                    "BREAKDOWN_DRAFT_REVISION_CONFLICT",
                    "草稿已发生变化，请刷新后再保存",
                    {"expected_revision": expected_revision, "actual_revision": int(draft["revision"])},
                )
            if connection.execute(
                "SELECT 1 FROM script_breakdown_scene_applications WHERE breakdown_draft_id=? AND scene_no=?",
                (draft_id, scene_no),
            ).fetchone():
                raise DomainRuleError("BREAKDOWN_SCENE_ALREADY_APPLIED", "已应用场次不可再修改", {"scene_no": scene_no})

            effective, parent = load_effective_breakdown_draft(connection, draft)
            scenes = effective.get("scenes")
            if not isinstance(scenes, list):
                raise DomainRuleError("BREAKDOWN_DRAFT_NOT_READY", "剧本拆解草稿不包含场次建议")
            index = next(
                (position for position, candidate in enumerate(scenes) if isinstance(candidate, dict) and int(candidate.get("scene_no", 0)) == scene_no),
                None,
            )
            if index is None:
                raise DomainRuleError("BREAKDOWN_SCENE_NOT_FOUND", "草稿中不存在选定场次", {"scene_no": scene_no})
            original_scene = scenes[index]
            replacement = self._validated_replacement(scene_no, original_scene, scene)
            revised = json.loads(_json(effective))
            revised["scenes"][index] = replacement

            confidence = json.loads(str(draft["confidence_json"] or "{}"))
            revised_scenes = revised["scenes"]
            grounding_contract = isinstance(confidence, dict) and (
                confidence.get("dialogue_grounding_status") == "PASS"
                or confidence.get("duration_contract_status") == "PASS"
            )
            if grounding_contract:
                validate_scene_dialogue_grounding(revised_scenes, confidence.get("source_passages"))
                validate_scene_source_grounding(revised_scenes, confidence.get("source_passages"))
                validate_scene_distinctness(revised_scenes)
            self._validate_duration_contract(revised_scenes, confidence)

            revision_no = int(parent["revision_no"]) + 1 if parent else 1
            revision_id = str(uuid.uuid4())
            now = _now()
            content_sha256 = _sha256(revised)
            connection.execute(
                """INSERT INTO script_breakdown_draft_revisions
                (id,breakdown_draft_id,revision_no,parent_revision_id,draft_json,content_sha256,change_note,created_at,created_by)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (revision_id, draft_id, revision_no, parent["id"] if parent else None, _json(revised), content_sha256, change_note.strip(), now, actor),
            )
            updated = connection.execute(
                """UPDATE script_breakdown_drafts SET updated_at=?,revision=revision+1
                WHERE id=? AND revision=? AND status='DRAFT_READY'""",
                (now, draft_id, expected_revision),
            )
            if updated.rowcount != 1:
                raise DomainRuleError("BREAKDOWN_DRAFT_REVISION_CONFLICT", "草稿已发生变化，请刷新后再保存")
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'writer','SCRIPT_BREAKDOWN_SCENE_REVISED','script_breakdown_draft_revision',?,'人工修订 AI 拆解草稿场次',?)""",
                (actor, revision_id, _json({"draft_id": draft_id, "scene_no": scene_no, "revision_no": revision_no, "content_sha256": content_sha256, "change_note": change_note.strip()})),
            )
        return {
            "draft_id": draft_id,
            "scene_no": scene_no,
            "draft_revision": expected_revision + 1,
            "effective_draft_revision_id": revision_id,
            "effective_draft_revision_no": revision_no,
            "content_sha256": content_sha256,
            "draft": revised,
            "human_edited": True,
        }

    @staticmethod
    def _validated_replacement(scene_no: int, original: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
        title = str(value.get("title") or "").strip()
        summary = str(value.get("summary") or "").strip()
        characters_raw = value.get("characters")
        shots = value.get("shots")
        if not title or len(title) > 200:
            raise DomainRuleError("BREAKDOWN_SCENE_TITLE_INVALID", "场次标题需为 1–200 个字符")
        if len(summary) > 4000:
            raise DomainRuleError("BREAKDOWN_SCENE_SUMMARY_INVALID", "场次摘要不能超过 4000 个字符")
        if not isinstance(characters_raw, list) or len(characters_raw) > 100:
            raise DomainRuleError("BREAKDOWN_SCENE_CHARACTERS_INVALID", "出场角色必须是至多 100 项的列表")
        characters = [str(name).strip() for name in characters_raw if str(name).strip()]
        if any(len(name) > 120 for name in characters):
            raise DomainRuleError("BREAKDOWN_SCENE_CHARACTERS_INVALID", "角色名称不能超过 120 个字符")
        original_shots = original.get("shots")
        if not isinstance(shots, list) or not isinstance(original_shots, list) or len(shots) != len(original_shots):
            raise DomainRuleError("BREAKDOWN_SCENE_STRUCTURE_LOCKED", "修订时不能增删镜头")
        original_nos = [int(shot.get("shot_no", 0)) for shot in original_shots if isinstance(shot, dict)]
        replacement: list[dict[str, Any]] = []
        for position, shot in enumerate(shots):
            if not isinstance(shot, dict) or position >= len(original_nos) or int(shot.get("shot_no", 0)) != original_nos[position]:
                raise DomainRuleError("BREAKDOWN_SCENE_STRUCTURE_LOCKED", "修订时不能修改镜头编号或顺序")
            duration = validated_breakdown_shot_duration(
                shot.get("duration_seconds"),
                error_code="BREAKDOWN_SHOT_DURATION_INVALID",
            )
            visual = str(shot.get("visual") or "").strip()
            action = str(shot.get("action") or "").strip()
            dialogue = shot.get("dialogue", "")
            if len(visual) > 4000 or len(action) > 4000 or len(str(dialogue)) > 8000:
                raise DomainRuleError("BREAKDOWN_SHOT_FIELD_TOO_LONG", "镜头字段长度超出限制")
            replacement.append({"shot_no": original_nos[position], "visual": visual, "action": action, "dialogue": dialogue, "duration_seconds": duration})
        return {"scene_no": scene_no, "title": title, "summary": summary, "characters": characters, "shots": replacement}

    @staticmethod
    def _validate_duration_contract(scenes: list[Any], confidence: Any) -> None:
        if not isinstance(confidence, dict) or confidence.get("duration_contract_status") != "PASS":
            return
        target = float(confidence.get("target_duration_seconds") or 0)
        if target <= 0:
            return
        total = sum(float(shot.get("duration_seconds", 0)) for scene in scenes if isinstance(scene, dict) for shot in (scene.get("shots") or []) if isinstance(shot, dict))
        tolerance = float(confidence.get("duration_tolerance_ratio", 0.2))
        if total < target * (1 - tolerance) or total > target * (1 + tolerance):
            raise DomainRuleError(
                "BREAKDOWN_DURATION_CONTRACT_MISMATCH",
                "修订后的总时长不符合目标分集合约",
                {"target_duration_seconds": target, "total_duration_seconds": total, "duration_tolerance_ratio": tolerance},
            )

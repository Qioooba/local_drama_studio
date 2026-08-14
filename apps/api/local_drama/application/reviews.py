"""Selection, human review and machine-QC commands for immutable media versions."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

from .media import MediaService


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "code": "image_asset",
        "version_no": 1,
        "subject_type": "MEDIA_VERSION",
        "items": [
            {"id": "identity", "label": "人物身份", "required": True},
            {"id": "clothing", "label": "服装与造型", "required": True},
            {"id": "anatomy", "label": "人体/手部", "required": True},
            {"id": "scene", "label": "场景与道具", "required": True},
            {"id": "composition", "label": "构图", "required": True},
            {"id": "lighting", "label": "光线", "required": True},
            {"id": "continuity", "label": "连续性", "required": True},
            {"id": "video_ready", "label": "可视频化", "required": True},
        ],
    },
    {
        "code": "proxy_video",
        "version_no": 1,
        "subject_type": "MEDIA_VERSION",
        "items": [
            {"id": "identity", "label": "身份一致", "required": True},
            {"id": "motion", "label": "动作可用", "required": True},
            {"id": "continuity", "label": "连续性", "required": True},
            {"id": "technical", "label": "技术可播放", "required": True},
        ],
    },
    {
        "code": "formal_video",
        "version_no": 1,
        "subject_type": "MEDIA_VERSION",
        "items": [
            {"id": "decode", "label": "可解码", "required": True},
            {"id": "dimensions", "label": "尺寸", "required": True},
            {"id": "fps", "label": "帧率", "required": True},
            {"id": "duration", "label": "时长", "required": True},
            {"id": "codec", "label": "编码", "required": True},
            {"id": "action", "label": "动作", "required": True},
            {"id": "identity", "label": "身份", "required": True},
            {"id": "flicker", "label": "闪烁", "required": True},
            {"id": "subtitle_safe_zone", "label": "字幕安全区", "required": True},
        ],
    },
    {
        "code": "episode_render",
        "version_no": 1,
        "subject_type": "EPISODE_RENDER_VERSION",
        "items": [
            {"id": "decode", "label": "可解码", "required": True},
            {"id": "timeline_inputs", "label": "时间线输入完整", "required": True},
            {"id": "audio_mix", "label": "音轨混音", "required": True},
            {"id": "subtitles", "label": "字幕与安全区", "required": True},
            {"id": "delivery_ready", "label": "本地交付可复核", "required": True},
        ],
    },
)


class ReviewService:
    def __init__(self, database: Database, settings: Settings | None = None) -> None:
        self.database = database
        self.settings = settings
        self.media = MediaService(database, settings) if settings is not None else None

    def ensure_templates(self, actor: str = "system") -> int:
        now = _utc_now()
        with self.database.transaction() as connection:
            for template in TEMPLATES:
                template_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"local-drama:review-template:{template['code']}:{template['version_no']}"))
                connection.execute(
                    """INSERT INTO review_templates (id, code, version_no, subject_type, items_json, created_at, updated_at, created_by, revision, schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'v2')
                    ON CONFLICT(code, version_no) DO UPDATE SET items_json=excluded.items_json, updated_at=excluded.updated_at,
                    revision=review_templates.revision+1""",
                    (template_id, template["code"], template["version_no"], template["subject_type"], _json(template["items"]), now, now, actor),
                )
        return len(TEMPLATES)

    def templates(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM review_templates ORDER BY code, version_no").fetchall()
        return [{**dict(row), "items": json.loads(row["items_json"])} for row in rows]

    def _media(self, media_version_id: str) -> dict[str, Any]:
        if self.media is None:
            raise DomainRuleError("MEDIA_SERVICE_UNAVAILABLE", "媒体服务未配置")
        return self.media.get_version(media_version_id)

    def _template_for_media(self, media: dict[str, Any]) -> dict[str, Any]:
        code = (
            "formal_video"
            if media["stage"] == "FORMAL" and media["media_kind"] == "VIDEO"
            else "proxy_video"
            if media["media_kind"] == "VIDEO"
            else "image_asset"
        )
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM review_templates WHERE code=? AND version_no=1", (code,)).fetchone()
        if row is None:
            raise DomainRuleError("REVIEW_TEMPLATE_NOT_FOUND", "审核模板尚未初始化", {"code": code})
        return {**dict(row), "items": json.loads(row["items_json"])}

    def select_version(self, media_version_id: str, selection_type: str, actor: str = "local-user") -> dict[str, Any]:
        if selection_type not in {"KEYFRAME", "PROXY_WINNER", "FORMAL_SELECTION"}:
            raise DomainRuleError("INVALID_SELECTION_TYPE", "选择类型必须是 KEYFRAME、PROXY_WINNER 或 FORMAL_SELECTION")
        media = self._media(media_version_id)
        if selection_type == "KEYFRAME" and (media["media_kind"] != "IMAGE" or media["stage"] != "KEYFRAME"):
            raise DomainRuleError("INVALID_KEYFRAME_SELECTION", "只有 KEYFRAME 阶段的图片版本可以成为关键帧选择")
        if selection_type == "PROXY_WINNER" and media["stage"] != "PROXY":
            raise DomainRuleError("INVALID_PROXY_WINNER", "只有 PROXY 版本可以成为代理 winner")
        if selection_type == "FORMAL_SELECTION" and media["stage"] != "FORMAL":
            raise DomainRuleError("INVALID_FORMAL_SELECTION", "正式选择必须引用 FORMAL 版本")
        now = _utc_now()
        selection_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            asset = connection.execute("SELECT id, revision FROM media_assets WHERE id=?", (media["media_asset_id"],)).fetchone()
            if asset is None:
                raise DomainRuleError("MEDIA_ASSET_NOT_FOUND", "媒体资产不存在")
            connection.execute(
                "INSERT INTO selections (id, media_asset_id, media_version_id, selection_type, source_revision, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'v2')",
                (selection_id, media["media_asset_id"], media_version_id, selection_type, asset["revision"], now, now, actor),
            )
            connection.execute(
                "UPDATE media_assets SET selected_version_id=?, version_counter=version_counter+1, revision=revision+1, updated_at=? WHERE id=?",
                (media_version_id, now, media["media_asset_id"]),
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, before_revision, after_revision, summary, metadata_redacted_json) VALUES (?, 'producer', 'MEDIA_SELECTED', 'media_asset', ?, ?, ?, ?, ?)",
                (
                    actor,
                    media["media_asset_id"],
                    asset["revision"],
                    asset["revision"] + 1,
                    f"选择 {selection_type}",
                    _json({"media_version_id": media_version_id, "selection_type": selection_type}),
                ),
            )
        return {
            "id": selection_id,
            "media_asset_id": media["media_asset_id"],
            "media_version_id": media_version_id,
            "selection_type": selection_type,
            "status": "SELECTED",
        }

    def _subject_revision(self, media: dict[str, Any]) -> int:
        with self.database.connect() as connection:
            row = connection.execute("SELECT revision FROM media_assets WHERE id=?", (media["media_asset_id"],)).fetchone()
        if row is None:
            raise DomainRuleError("MEDIA_ASSET_NOT_FOUND", "媒体资产不存在")
        return int(row["revision"])

    def _latest_machine_status(self, media_version_id: str) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT status FROM machine_check_runs WHERE subject_type='MEDIA_VERSION' AND subject_id=? ORDER BY created_at DESC LIMIT 1",
                (media_version_id,),
            ).fetchone()
        return str(row["status"]) if row else None

    def submit_episode_render_review(
        self,
        render_id: str,
        template_version_id: str,
        decision: str,
        expected_subject_revision: int,
        checks: list[dict[str, Any]],
        comment: str | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Submit a formal review for a verified immutable episode render.

        Episode renders are not media assets and therefore cannot use the
        MEDIA_VERSION review path.  They still use the same immutable review
        decision/check tables so G8 can require an auditable approval without
        mutating the render or pretending a machine check is a human decision.
        """
        if decision not in {"APPROVED", "REJECTED", "NEEDS_CHANGES"}:
            raise DomainRuleError("INVALID_REVIEW_DECISION", "审核决定无效")
        with self.database.connect() as connection:
            render = connection.execute(
                """SELECT erv.*, p.root_rel FROM episode_render_versions erv
                JOIN episodes e ON e.id=erv.episode_id
                JOIN seasons s ON s.id=e.season_id
                JOIN projects p ON p.id=s.project_id
                WHERE erv.id=?""",
                (render_id,),
            ).fetchone()
            template = connection.execute(
                "SELECT * FROM review_templates WHERE id=? AND subject_type='EPISODE_RENDER_VERSION'",
                (template_version_id,),
            ).fetchone()
        if render is None:
            raise DomainRuleError("EPISODE_RENDER_NOT_FOUND", "整集渲染版本不存在")
        if template is None:
            raise DomainRuleError("REVIEW_TEMPLATE_NOT_FOUND", "整集渲染审核模板不存在")
        if int(render["revision"]) != expected_subject_revision:
            raise DomainRuleError(
                "REVIEW_STALE",
                "审核基于旧的整集渲染 revision",
                {"current_revision": int(render["revision"]), "submitted_revision": expected_subject_revision},
            )
        if str(render["integrity_status"]) != "VERIFIED":
            raise DomainRuleError("EPISODE_RENDER_NOT_VERIFIED", "只有完整性 VERIFIED 的整集渲染可以审核")
        if self.settings is None:
            raise DomainRuleError("MEDIA_SERVICE_UNAVAILABLE", "本地设置未配置")
        project_root = (self.settings.projects_root / str(render["root_rel"])).resolve()
        render_path = (project_root / str(render["rel_path"])).resolve()
        if not render_path.is_relative_to(project_root) or not render_path.is_file() or render_path.is_symlink():
            raise DomainRuleError("EPISODE_RENDER_FILE_MISSING", "整集渲染文件缺失或路径越界")
        digest = hashlib.sha256(render_path.read_bytes()).hexdigest()
        if not hmac.compare_digest(digest, str(render["sha256"])):
            raise DomainRuleError("EPISODE_RENDER_INTEGRITY_FAILED", "整集渲染文件 hash 与登记值不一致")
        required = {str(item["id"]) for item in json.loads(template["items_json"]) if item.get("required", True)}
        submitted = {str(item.get("item_id")) for item in checks}
        missing = sorted(required - submitted)
        if missing:
            raise DomainRuleError("REVIEW_CHECKS_INCOMPLETE", "审核检查项不完整", {"missing": missing})
        failures = sorted(str(item["item_id"]) for item in checks if item.get("result") != "PASS")
        if decision == "APPROVED" and failures:
            raise DomainRuleError("REVIEW_CHECK_FAILED", "存在未通过检查项，不能批准", {"failed": failures})
        review_id = str(uuid.uuid4())
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO review_decisions
                (id, subject_type, subject_id, review_template_version_id, decision, comment,
                 subject_revision, is_stale, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, 'EPISODE_RENDER_VERSION', ?, ?, ?, ?, ?, 0, ?, ?, ?, 1, 'v2')""",
                (review_id, render_id, template_version_id, decision, comment, expected_subject_revision, now, now, actor),
            )
            for item in checks:
                connection.execute(
                    "INSERT INTO review_checks (id, review_decision_id, item_id, result, comment) VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), review_id, str(item["item_id"]), str(item["result"]), item.get("comment")),
                )
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, before_revision, after_revision, summary, metadata_redacted_json)
                VALUES (?, 'reviewer', 'EPISODE_RENDER_REVIEW_SUBMITTED', 'episode_render_version', ?, ?, ?, ?, ?)""",
                (actor, render_id, expected_subject_revision, expected_subject_revision, f"整集渲染审核 {decision}", _json({"review_id": review_id, "template_version_id": template_version_id})),
            )
        return {
            "id": review_id,
            "subject_type": "EPISODE_RENDER_VERSION",
            "subject_id": render_id,
            "decision": decision,
            "is_stale": False,
            "subject_revision": expected_subject_revision,
        }

    def _approval_impact(self, connection: Any, media: dict[str, Any]) -> dict[str, Any]:
        asset = connection.execute(
            "SELECT revision, approved_version_id FROM media_assets WHERE id=?", (media["media_asset_id"],)
        ).fetchone()
        if asset is None:
            raise DomainRuleError("MEDIA_ASSET_NOT_FOUND", "媒体资产不存在")
        old_approved_id = asset["approved_version_id"]
        changes_boundary = (
            media.get("owner_type") == "SHOT"
            and old_approved_id is not None
            and str(old_approved_id) != str(media["id"])
        )
        constraints: list[str] = []
        variants: list[str] = []
        anchors: list[str] = []
        stale_reason: str | None = None
        if changes_boundary:
            incoming_constraints = [
                str(row["id"])
                for row in connection.execute(
                    """SELECT id FROM shot_transition_constraints
                    WHERE to_shot_id=? AND constraint_type='END_AT_NEXT_FIRST'
                    AND enforcement IN ('REQUIRED', 'HARD') AND is_stale=0 ORDER BY id""",
                    (media["owner_id"],),
                ).fetchall()
            ]
            first_frame_variants = [
                str(row["id"])
                for row in connection.execute(
                    """SELECT DISTINCT gv.id FROM generation_variants gv
                    JOIN generation_intents gi ON gi.id=gv.intent_id
                    JOIN variant_input_bindings vib ON vib.variant_id=gv.id
                    WHERE gi.owner_type='SHOT' AND gi.owner_id=? AND vib.role='FIRST_FRAME'
                    AND vib.media_version_id=? AND gv.is_stale=0 ORDER BY gv.id""",
                    (media["owner_id"], old_approved_id),
                ).fetchall()
            ]
            anchors = [
                str(row["id"])
                for row in connection.execute(
                    """SELECT id FROM frame_anchors
                    WHERE source_media_version_id=? AND role_hint='LAST_FRAME' AND is_stale=0 ORDER BY id""",
                    (old_approved_id,),
                ).fetchall()
            ]
            if anchors:
                placeholders = ",".join("?" for _ in anchors)
                constraints.extend(
                    str(row["id"])
                    for row in connection.execute(
                        f"""SELECT id FROM shot_transition_constraints
                        WHERE from_anchor_id IN ({placeholders}) AND enforcement IN ('REQUIRED', 'HARD')
                        AND is_stale=0 ORDER BY id""",
                        anchors,
                    ).fetchall()
                )
                variants.extend(
                    str(row["id"])
                    for row in connection.execute(
                        f"""SELECT DISTINCT gv.id FROM generation_variants gv
                        JOIN variant_input_bindings vib ON vib.variant_id=gv.id
                        JOIN frame_anchors fa ON fa.extracted_media_version_id=vib.media_version_id
                        WHERE fa.id IN ({placeholders}) AND gv.is_stale=0 ORDER BY gv.id""",
                        anchors,
                    ).fetchall()
                )
            constraints = sorted(set([*incoming_constraints, *constraints]))
            variants = sorted(set([*first_frame_variants, *variants]))
            stale_reason = "approved_video_winner_changed" if anchors else "approved_first_frame_changed"
        snapshot = {
            "media_asset_id": str(media["media_asset_id"]),
            "media_asset_revision": int(asset["revision"]),
            "new_approved_media_version_id": str(media["id"]),
            "old_approved_media_version_id": str(old_approved_id) if old_approved_id else None,
            "shot_id": str(media["owner_id"]) if media.get("owner_type") == "SHOT" else None,
            "frame_anchor_ids": anchors,
            "transition_constraint_ids": constraints,
            "dependent_variant_ids": variants,
            "stale_reason": stale_reason,
        }
        return {
            **snapshot,
            "would_mark_stale": bool(anchors or constraints or variants),
            "frame_anchor_count": len(anchors),
            "transition_count": len(constraints),
            "variant_count": len(variants),
            "plan_hash": hashlib.sha256(_json(snapshot).encode("utf-8")).hexdigest(),
        }

    def preview_approval_impact(self, media_version_id: str) -> dict[str, Any]:
        media = self._media(media_version_id)
        with self.database.connect() as connection:
            return self._approval_impact(connection, media)

    def submit_review(
        self,
        media_version_id: str,
        template_version_id: str,
        decision: str,
        expected_subject_revision: int,
        checks: list[dict[str, Any]],
        comment: str | None = None,
        actor: str = "local-user",
        continuity_plan_hash: str | None = None,
    ) -> dict[str, Any]:
        if decision not in {"APPROVED", "REJECTED", "NEEDS_CHANGES"}:
            raise DomainRuleError("INVALID_REVIEW_DECISION", "审核决定无效")
        media = self._media(media_version_id)
        if decision == "APPROVED" and str(media.get("approved_version_id") or "") == media_version_id:
            with self.database.connect() as connection:
                existing_approval = connection.execute(
                    """SELECT * FROM review_decisions WHERE subject_type='MEDIA_VERSION' AND subject_id=?
                    AND decision='APPROVED' AND is_stale=0 ORDER BY created_at DESC LIMIT 1""",
                    (media_version_id,),
                ).fetchone()
            if existing_approval is not None:
                return {
                    "id": str(existing_approval["id"]),
                    "subject_type": "MEDIA_VERSION",
                    "subject_id": media_version_id,
                    "decision": "APPROVED",
                    "is_stale": False,
                    "subject_revision": int(existing_approval["subject_revision"]),
                    "continuity_impact": None,
                    "duplicate": True,
                }
        subject_revision = self._subject_revision(media)
        if subject_revision != expected_subject_revision:
            raise DomainRuleError(
                "REVIEW_STALE", "审核基于旧的媒体资产 revision", {"current_revision": subject_revision, "submitted_revision": expected_subject_revision}
            )
        with self.database.connect() as connection:
            template = connection.execute("SELECT * FROM review_templates WHERE id=?", (template_version_id,)).fetchone()
        if template is None:
            raise DomainRuleError("REVIEW_TEMPLATE_NOT_FOUND", "审核模板版本不存在")
        required = {str(item["id"]) for item in json.loads(template["items_json"]) if item.get("required", True)}
        submitted = {str(item.get("item_id")) for item in checks}
        missing = sorted(required - submitted)
        if missing:
            raise DomainRuleError("REVIEW_CHECKS_INCOMPLETE", "审核检查项不完整", {"missing": missing})
        failures = sorted(str(item["item_id"]) for item in checks if item.get("result") != "PASS")
        if decision == "APPROVED" and failures:
            raise DomainRuleError("REVIEW_CHECK_FAILED", "存在未通过检查项，不能批准", {"failed": failures})
        if decision == "APPROVED" and media["stage"] == "FORMAL" and self._latest_machine_status(media_version_id) != "PASS":
            raise DomainRuleError("MACHINE_QC_REQUIRED", "正式媒体必须先通过机器 QC")
        review_id = str(uuid.uuid4())
        now = _utc_now()
        with self.database.transaction() as connection:
            current_asset = connection.execute(
                "SELECT revision, approved_version_id FROM media_assets WHERE id=?", (media["media_asset_id"],)
            ).fetchone()
            if current_asset is None:
                raise DomainRuleError("MEDIA_ASSET_NOT_FOUND", "媒体资产不存在")
            if int(current_asset["revision"]) != expected_subject_revision:
                raise DomainRuleError(
                    "REVIEW_STALE",
                    "审核基于旧的媒体资产 revision",
                    {"current_revision": int(current_asset["revision"]), "submitted_revision": expected_subject_revision},
                )
            transactional_media = {
                **media,
                "revision": int(current_asset["revision"]),
                "approved_version_id": current_asset["approved_version_id"],
            }
            impact = self._approval_impact(connection, transactional_media) if decision == "APPROVED" else None
            if impact and impact["would_mark_stale"]:
                if continuity_plan_hash is None:
                    raise DomainRuleError(
                        "CONTINUITY_IMPACT_CONFIRMATION_REQUIRED",
                        "批准会使连续性约束或下游 Variant 失效；请先预览影响并确认 plan hash",
                        {"impact": impact},
                    )
                if not hmac.compare_digest(str(impact["plan_hash"]), continuity_plan_hash):
                    raise DomainRuleError(
                        "CONTINUITY_IMPACT_STALE",
                        "连续性影响已变化，请重新预览",
                        {"current_plan_hash": impact["plan_hash"]},
                    )
            connection.execute(
                "INSERT INTO review_decisions (id, subject_type, subject_id, review_template_version_id, decision, comment, subject_revision, is_stale, created_at, updated_at, created_by, revision, schema_version) VALUES (?, 'MEDIA_VERSION', ?, ?, ?, ?, ?, 0, ?, ?, ?, 1, 'v2')",
                (review_id, media_version_id, template_version_id, decision, comment, subject_revision, now, now, actor),
            )
            for item in checks:
                connection.execute(
                    "INSERT INTO review_checks (id, review_decision_id, item_id, result, comment) VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), review_id, str(item["item_id"]), str(item["result"]), item.get("comment")),
                )
            if decision == "APPROVED":
                connection.execute(
                    "UPDATE media_assets SET approved_version_id=?, revision=revision+1, updated_at=? WHERE id=?",
                    (media_version_id, now, media["media_asset_id"]),
                )
                if impact and impact["frame_anchor_ids"]:
                    placeholders = ",".join("?" for _ in impact["frame_anchor_ids"])
                    connection.execute(
                        f"""UPDATE frame_anchors SET is_stale=1, stale_reason=?, revision=revision+1, updated_at=?
                        WHERE id IN ({placeholders})""",
                        (impact["stale_reason"], now, *impact["frame_anchor_ids"]),
                    )
                if impact and impact["transition_constraint_ids"]:
                    placeholders = ",".join("?" for _ in impact["transition_constraint_ids"])
                    connection.execute(
                        f"""UPDATE shot_transition_constraints SET is_stale=1,
                        stale_reason=?, compatibility_status='STALE',
                        boundary_revision=boundary_revision+1, revision=revision+1, updated_at=?
                        WHERE id IN ({placeholders})""",
                        (impact["stale_reason"], now, *impact["transition_constraint_ids"]),
                    )
                if impact and impact["dependent_variant_ids"]:
                    placeholders = ",".join("?" for _ in impact["dependent_variant_ids"])
                    connection.execute(
                        f"""UPDATE generation_variants SET is_stale=1,
                        stale_reason=?, revision=revision+1, updated_at=?
                        WHERE id IN ({placeholders})""",
                        (impact["stale_reason"], now, *impact["dependent_variant_ids"]),
                    )
                if impact and impact["would_mark_stale"]:
                    connection.execute(
                        """INSERT INTO outbox_events (type, project_id, subject_type, subject_id, payload_json)
                        VALUES ('CONTINUITY_STALE_PROPAGATED', ?, 'MEDIA_VERSION', ?, ?)""",
                        (media["project_id"], media_version_id, _json(impact)),
                    )
                    connection.execute(
                        """INSERT INTO audit_events
                        (actor, role_context, action, subject_type, subject_id, before_revision, after_revision, summary, metadata_redacted_json)
                        VALUES (?, 'reviewer', 'CONTINUITY_STALE_PROPAGATED', 'media_version', ?, ?, ?, ?, ?)""",
                        (
                            actor,
                            media_version_id,
                            expected_subject_revision,
                            expected_subject_revision + 1,
                            "批准新边界媒体并事务化传播连续性失效",
                            _json(impact),
                        ),
                    )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, before_revision, after_revision, summary, metadata_redacted_json) VALUES (?, 'reviewer', 'REVIEW_SUBMITTED', 'media_version', ?, ?, ?, ?, ?)",
                (
                    actor,
                    media_version_id,
                    subject_revision,
                    subject_revision + 1,
                    f"审核 {decision}",
                    _json({"review_id": review_id, "decision": decision, "template_version_id": template_version_id}),
                ),
            )
        return {
            "id": review_id,
            "subject_type": "MEDIA_VERSION",
            "subject_id": media_version_id,
            "decision": decision,
            "is_stale": False,
            "subject_revision": subject_revision,
            "continuity_impact": impact,
        }

    def machine_check(self, media_version_id: str, policy_version: str = "g4_media_qc_v1", actor: str = "system") -> dict[str, Any]:
        media = self._media(media_version_id)
        if self.media is None:
            raise DomainRuleError("MEDIA_SERVICE_UNAVAILABLE", "媒体服务未配置")
        _, path = self.media.content_path(media_version_id)
        results: list[dict[str, Any]] = []
        file_ok = path.is_file() and path.stat().st_size == int(media["byte_size"])
        results.append(
            {
                "item_id": "file_integrity",
                "result": "PASS" if file_ok else "FAIL",
                "details": {"exists": path.is_file(), "byte_size": path.stat().st_size if path.exists() else None},
            }
        )
        probe = media["probe"]
        probe_ok = media["media_kind"] in {"DOCUMENT"} or probe.get("probe_status") == "PASS"
        results.append({"item_id": "decode", "result": "PASS" if probe_ok else "FAIL", "details": {"probe_status": probe.get("probe_status")}})
        status = "PASS" if all(item["result"] == "PASS" for item in results) else "FAIL"
        run_id = str(uuid.uuid4())
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO machine_check_runs (id, subject_type, subject_id, policy_version, status, created_at, updated_at, created_by, revision, schema_version) VALUES (?, 'MEDIA_VERSION', ?, ?, ?, ?, ?, ?, 1, 'v2')",
                (run_id, media_version_id, policy_version, status, now, now, actor),
            )
            for item in results:
                connection.execute(
                    "INSERT INTO machine_check_results (id, run_id, item_id, result, details_json) VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), run_id, item["item_id"], item["result"], _json(item["details"])),
                )
        return {
            "id": run_id,
            "subject_type": "MEDIA_VERSION",
            "subject_id": media_version_id,
            "policy_version": policy_version,
            "status": status,
            "results": results,
        }

    def review_context(self, media_version_id: str) -> dict[str, Any]:
        media = self._media(media_version_id)
        template = self._template_for_media(media)
        with self.database.connect() as connection:
            selections = connection.execute("SELECT * FROM selections WHERE media_asset_id=? ORDER BY created_at DESC", (media["media_asset_id"],)).fetchall()
            reviews = connection.execute(
                "SELECT * FROM review_decisions WHERE subject_type='MEDIA_VERSION' AND subject_id=? ORDER BY created_at DESC", (media_version_id,)
            ).fetchall()
            machine = connection.execute(
                "SELECT * FROM machine_check_runs WHERE subject_type='MEDIA_VERSION' AND subject_id=? ORDER BY created_at DESC", (media_version_id,)
            ).fetchall()
        return {
            "media_version": media,
            "subject_revision": self._subject_revision(media),
            "template": template,
            "selections": [dict(row) for row in selections],
            "reviews": [dict(row) for row in reviews],
            "machine_checks": [dict(row) for row in machine],
        }

    def list_reviews(self, subject_type: str, subject_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            decisions = connection.execute(
                "SELECT * FROM review_decisions WHERE subject_type=? AND subject_id=? ORDER BY created_at DESC", (subject_type, subject_id)
            ).fetchall()
            result: list[dict[str, Any]] = []
            for decision in decisions:
                checks = connection.execute(
                    "SELECT item_id, result, comment FROM review_checks WHERE review_decision_id=? ORDER BY item_id", (decision["id"],)
                ).fetchall()
                annotations = connection.execute(
                    "SELECT time_us, annotation_type, note, frame_rel FROM review_annotations WHERE review_decision_id=? ORDER BY created_at", (decision["id"],)
                ).fetchall()
                result.append({**dict(decision), "checks": [dict(item) for item in checks], "annotations": [dict(item) for item in annotations]})
        return result

    def inbox(self, project_id: str | None = None, media_kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        params: list[Any] = []
        where: list[str] = []
        if project_id:
            where.append("ma.project_id=?")
            params.append(project_id)
        if media_kind:
            where.append("ma.media_kind=?")
            params.append(media_kind)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(max(1, min(limit, 500)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""SELECT mv.id AS media_version_id, mv.media_asset_id, mv.version_no, mv.stage, mv.rel_path, mv.mime_type,
                mv.sha256, ma.project_id, ma.media_kind, ma.selected_version_id, ma.approved_version_id,
                rd.id AS review_id, rd.decision, rd.is_stale, rd.created_at AS reviewed_at
                FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
                LEFT JOIN review_decisions rd ON rd.id=(SELECT r2.id FROM review_decisions r2 WHERE r2.subject_type='MEDIA_VERSION' AND r2.subject_id=mv.id ORDER BY r2.created_at DESC LIMIT 1)
                {clause} AND (rd.id IS NULL OR rd.decision != 'APPROVED' OR rd.is_stale=1)
                ORDER BY COALESCE(rd.created_at, mv.created_at), mv.created_at LIMIT ?""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_stale_for_owner(self, owner_id: str, reason: str, actor: str = "system") -> int:
        now = _utc_now()
        with self.database.transaction() as connection:
            result = connection.execute(
                """UPDATE review_decisions SET is_stale=1, stale_reason=?, updated_at=?, revision=revision+1
                WHERE subject_type='MEDIA_VERSION' AND subject_id IN (SELECT mv.id FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id WHERE ma.owner_id=?)""",
                (reason, now, owner_id),
            )
            if result.rowcount:
                connection.execute(
                    "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'system', 'REVIEWS_MARKED_STALE', 'owner', ?, ?, ?)",
                    (actor, owner_id, "上游 revision 变更，审核标记 stale", _json({"count": result.rowcount, "reason": reason})),
                )
            return result.rowcount

    def void_review(self, review_id: str, actor: str = "local-user") -> dict[str, Any]:
        now = _utc_now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM review_decisions WHERE id=?", (review_id,)).fetchone()
            if row is None:
                raise DomainRuleError("REVIEW_NOT_FOUND", "审核记录不存在")
            connection.execute(
                "UPDATE review_decisions SET decision='VOIDED', is_stale=1, stale_reason='voided_by_user', updated_at=?, revision=revision+1 WHERE id=?",
                (now, review_id),
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'reviewer', 'REVIEW_VOIDED', 'review_decision', ?, ?, '{}')",
                (actor, review_id, "撤回审核记录"),
            )
        return {**dict(row), "decision": "VOIDED", "is_stale": 1}

    def batch_preflight(self, project_id: str, items: list[dict[str, Any]], actor: str = "local-user") -> dict[str, Any]:
        if not items:
            raise DomainRuleError("EMPTY_REVIEW_BATCH", "批量审核不能为空")
        plan_items: list[dict[str, Any]] = []
        for item in items:
            media = self._media(str(item["media_version_id"]))
            current = self._subject_revision(media)
            plan_items.append({"media_version_id": media["id"], "template_version_id": item["template_version_id"], "expected_subject_revision": current})
        token = secrets.token_urlsafe(32)
        token_hash = _token_hash(token)
        plan_id = str(uuid.uuid4())
        expires = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO review_batch_plans (id, token_hash, project_id, plan_json, expires_at, status, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, ?, 'READY', ?, ?, ?, 1, 'v2')",
                (plan_id, token_hash, project_id, _json(plan_items), expires, now, now, actor),
            )
        return {"plan_id": plan_id, "plan_token": token, "expires_at": expires, "items": plan_items, "status": "READY"}

    def batch_commit(self, token: str, decision: str, checks: list[dict[str, Any]], comment: str | None = None, actor: str = "local-user") -> dict[str, Any]:
        with self.database.connect() as connection:
            plan = connection.execute("SELECT * FROM review_batch_plans WHERE token_hash=?", (_token_hash(token),)).fetchone()
        if plan is None or plan["status"] != "READY" or datetime.fromisoformat(plan["expires_at"]) <= datetime.now(UTC):
            raise DomainRuleError("REVIEW_BATCH_TOKEN_INVALID", "批量审核 plan_token 无效、过期或已使用")
        plan_items = json.loads(plan["plan_json"])
        stale: list[dict[str, Any]] = []
        for item in plan_items:
            current = self._subject_revision(self._media(item["media_version_id"]))
            if current != item["expected_subject_revision"]:
                stale.append(
                    {
                        "media_version_id": item["media_version_id"],
                        "status": "STALE",
                        "current_revision": current,
                        "planned_revision": item["expected_subject_revision"],
                    }
                )
        if stale:
            raise DomainRuleError("REVIEW_BATCH_STALE", "批量预检后对象发生变化，不能继续批准", {"items": stale})
        results = []
        for item in plan_items:
            results.append(
                self.submit_review(item["media_version_id"], item["template_version_id"], decision, item["expected_subject_revision"], checks, comment, actor)
            )
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute("UPDATE review_batch_plans SET status='COMMITTED', updated_at=?, revision=revision+1 WHERE id=?", (now, plan["id"]))
        return {"plan_id": plan["id"], "status": "COMMITTED", "items": results}

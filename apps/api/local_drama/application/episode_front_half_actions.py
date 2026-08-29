"""Fail-closed front-half actions for an episode production workflow.

The service deliberately does not invent creative facts.  It projects the
existing source, breakdown, asset, identity-pack, shot-plan and review
authorities into automation-task reports.  A missing human decision is a
``NEEDS_HITL`` machine result, never an automatic approval or a synthetic
production row.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from local_drama.config import Settings
from local_drama.domain.character_identity_packs import REQUIRED_THREE_VIEW_SLOTS
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.policies import missing_shot_fields
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.filesystem.path_policy import controlled_path


def _decode(value: object, fallback: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


class EpisodeFrontHalfActionService:
    """Read-only/idempotent validators used by Production DAG front-half jobs."""

    ACTIONS = frozenset(
        {
            "STORY_PARSE",
            "SCRIPT_BREAKDOWN",
            "ASSET_IDENTITY",
            "ASSET_COMPLETION",
            "EPISODE_PLAN",
            "KEYFRAME_CHECK",
        }
    )

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    @staticmethod
    def _report(status: str, code: str, detail: str, evidence: dict[str, Any]) -> tuple[dict[str, Any], int]:
        ok = status in {"PASS", "SKIPPED"}
        machine_check = {
            "status": status,
            "ok": ok,
            "code": code,
            "detail": detail,
            "evidence_type": "AUTHORITATIVE_FACT_PROJECTION",
            "human_approval_created": False,
            **evidence,
        }
        return (
            {
                "status": status,
                "machine_check": machine_check,
                "produced": {"fact_refs": evidence.get("fact_refs", []), "writes": []},
                "summary": detail,
            },
            0,
        )

    def _episode(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT e.id,e.code,s.project_id,p.root_rel
                FROM episodes e JOIN seasons s ON s.id=e.season_id
                JOIN projects p ON p.id=s.project_id WHERE e.id=?""",
                (episode_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
        return dict(row)

    def _shots(self, episode_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT s.id,s.code,s.status,s.current_revision_id,sr.fields_json
                FROM shots s LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
                WHERE s.episode_id=? AND s.archived_at IS NULL
                ORDER BY CAST(s.order_key AS REAL),s.code""",
                (episode_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _committed_source(self, project_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT i.id AS import_session_id,i.status AS import_status,
                v.id AS source_document_version_id,v.parse_status,v.extracted_text_rel,
                v.text_sha256,v.sha256,v.source_document_id
                FROM import_sessions i
                JOIN source_document_versions v ON v.id=i.source_document_version_id
                WHERE i.project_id=? AND EXISTS (
                  SELECT 1 FROM audit_events ae
                  WHERE ae.action='IMPORT_SESSION_COMMITTED'
                  AND ae.subject_type='import_session' AND ae.subject_id=i.id
                )
                ORDER BY i.updated_at DESC,i.id DESC LIMIT 1""",
                (project_id,),
            ).fetchone()
        return dict(row) if row else None

    def _validate_source_file(self, episode: dict[str, Any], source: dict[str, Any]) -> tuple[bool, str]:
        if str(source.get("parse_status")) != "PARSED":
            return False, "提交的剧本源版本尚未完成可靠解析"
        project_root = self.settings.resolve_project_root(str(episode["root_rel"]))
        try:
            path = controlled_path(
                project_root,
                str(source.get("extracted_text_rel") or ""),
                must_exist=True,
                require_file=True,
                code="SOURCE_TEXT_INVALID",
            )
        except DomainRuleError:
            return False, "提交的不可变剧本文本缺失或路径无效"
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return False, "提交的不可变剧本文本无法读取"
        if digest != str(source.get("text_sha256") or ""):
            return False, "提交的不可变剧本文本 hash 已变化"
        return True, "剧本源提交与不可变解析文本完整"

    def _applied_breakdowns(self, project_id: str, episode_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT d.id,d.source_document_version_id,d.import_session_id,d.status,d.revision
                FROM script_breakdown_drafts d
                WHERE d.project_id=? AND d.status='APPLIED' AND EXISTS (
                  SELECT 1 FROM audit_events ae
                  WHERE ae.action='SCRIPT_BREAKDOWN_APPLIED'
                  AND ae.subject_type='script_breakdown_draft' AND ae.subject_id=d.id
                  AND json_extract(ae.metadata_redacted_json,'$.episode_id')=?
                ) ORDER BY d.updated_at DESC,d.id DESC""",
                (project_id, episode_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def _ready_breakdowns(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT id,source_document_version_id,import_session_id,status,revision
                FROM script_breakdown_drafts WHERE project_id=? AND status='DRAFT_READY'
                ORDER BY updated_at DESC,id DESC""",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def snapshot(self, episode_id: str) -> dict[str, Any]:
        """Return a bounded fingerprint input for recovery/stale detection."""
        episode = self._episode(episode_id)
        project_id = str(episode["project_id"])
        source = self._committed_source(project_id)
        applied = self._applied_breakdowns(project_id, episode_id)
        with self.database.connect() as connection:
            drafts = connection.execute(
                """SELECT id,status,revision,source_document_version_id,import_session_id
                FROM script_breakdown_drafts WHERE project_id=?
                ORDER BY id""",
                (project_id,),
            ).fetchall()
            proposals = connection.execute(
                """SELECT p.id,p.status,p.resolved_asset_id,p.revision
                FROM story_asset_proposals p
                WHERE p.breakdown_draft_id IN (
                  SELECT d.id FROM script_breakdown_drafts d
                  WHERE d.project_id=? AND d.status='APPLIED' AND EXISTS (
                    SELECT 1 FROM audit_events ae WHERE ae.action='SCRIPT_BREAKDOWN_APPLIED'
                    AND ae.subject_id=d.id
                    AND json_extract(ae.metadata_redacted_json,'$.episode_id')=?
                  )
                ) ORDER BY p.id""",
                (project_id, episode_id),
            ).fetchall()
            packs = connection.execute(
                """SELECT DISTINCT v.id,v.status,v.revision,p.current_version_id
                FROM character_identity_pack_versions v
                JOIN character_identity_packs p ON p.id=v.pack_id
                WHERE v.story_asset_id IN (
                  SELECT DISTINCT sab.asset_id FROM shot_asset_bindings sab
                  JOIN shots s ON s.id=sab.shot_id WHERE s.episode_id=? AND s.archived_at IS NULL
                  UNION SELECT DISTINCT p2.resolved_asset_id FROM story_asset_proposals p2
                  WHERE p2.resolved_asset_id IS NOT NULL AND p2.breakdown_draft_id IN (
                    SELECT d.id FROM script_breakdown_drafts d
                    WHERE d.project_id=? AND d.status='APPLIED' AND EXISTS (
                      SELECT 1 FROM audit_events ae WHERE ae.action='SCRIPT_BREAKDOWN_APPLIED'
                      AND ae.subject_id=d.id
                      AND json_extract(ae.metadata_redacted_json,'$.episode_id')=?
                    )
                  )
                ) ORDER BY v.id""",
                (episode_id, project_id, episode_id),
            ).fetchall()
            identity_bindings = connection.execute(
                """SELECT sab.shot_id,sab.asset_id,sab.identity_pack_version_id,sab.revision
                FROM shot_asset_bindings sab JOIN shots s ON s.id=sab.shot_id
                JOIN story_assets sa ON sa.id=sab.asset_id
                WHERE s.episode_id=? AND s.archived_at IS NULL AND sa.kind='CHARACTER'
                ORDER BY sab.shot_id,sab.asset_id,sab.role_in_shot""",
                (episode_id,),
            ).fetchall()
            keyframes = connection.execute(
                """SELECT s.id AS shot_id,ma.approved_version_id,
                rd.id AS review_decision_id,rd.revision AS review_revision,rd.is_stale
                FROM shots s LEFT JOIN media_assets ma
                  ON ma.owner_type='SHOT' AND ma.owner_id=s.id
                  AND ma.purpose='KEYFRAME' AND ma.media_kind='IMAGE'
                LEFT JOIN review_decisions rd
                  ON rd.subject_type='MEDIA_VERSION' AND rd.subject_id=ma.approved_version_id
                  AND rd.decision='APPROVED'
                WHERE s.episode_id=? AND s.archived_at IS NULL
                ORDER BY s.id,ma.id,rd.id""",
                (episode_id,),
            ).fetchall()
        return {
            "source": {
                "import_session_id": source.get("import_session_id"),
                "source_document_version_id": source.get("source_document_version_id"),
                "text_sha256": source.get("text_sha256"),
                "parse_status": source.get("parse_status"),
            } if source else None,
            "applied_breakdowns": [
                {"id": row["id"], "revision": row["revision"], "status": row["status"]} for row in applied
            ],
            "breakdown_drafts": [dict(row) for row in drafts],
            "proposals": [dict(row) for row in proposals],
            "identity_pack_versions": [dict(row) for row in packs],
            "shot_identity_bindings": [dict(row) for row in identity_bindings],
            "approved_keyframes": [dict(row) for row in keyframes],
            "shots": [
                {
                    "id": row["id"],
                    "status": row["status"],
                    "current_revision_id": row["current_revision_id"],
                }
                for row in self._shots(episode_id)
            ],
        }

    def story_parse(self, episode_id: str) -> tuple[dict[str, Any], int]:
        episode = self._episode(episode_id)
        source = self._committed_source(str(episode["project_id"]))
        if source is None:
            shots = self._shots(episode_id)
            if shots:
                return self._report(
                    "SKIPPED",
                    "EXISTING_EPISODE_PLAN_AUTHORITY",
                    "该集已有生产镜头事实；未伪造或覆盖历史剧本源",
                    {"shot_count": len(shots), "authority": "EXISTING_EPISODE_PLAN", "fact_refs": [str(row["id"]) for row in shots]},
                )
            return self._report(
                "NEEDS_HITL",
                "SOURCE_COMMIT_REQUIRED",
                "没有已人工确认提交的剧本源；请先导入、核对并提交预览",
                {"project_id": str(episode["project_id"]), "fact_refs": []},
            )
        valid, detail = self._validate_source_file(episode, source)
        status = "PASS" if valid else "NEEDS_HITL"
        return self._report(
            status,
            "SOURCE_COMMIT_VERIFIED" if valid else "SOURCE_AUTHORITY_INVALID",
            detail,
            {
                "import_session_id": str(source["import_session_id"]),
                "source_document_version_id": str(source["source_document_version_id"]),
                "text_sha256": str(source["text_sha256"]),
                "fact_refs": [str(source["import_session_id"]), str(source["source_document_version_id"])],
            },
        )

    def script_breakdown(self, episode_id: str) -> tuple[dict[str, Any], int]:
        episode = self._episode(episode_id)
        project_id = str(episode["project_id"])
        applied = self._applied_breakdowns(project_id, episode_id)
        if applied:
            return self._report(
                "PASS",
                "BREAKDOWN_APPLIED",
                "已找到经人工应用到本集的剧本拆解草稿",
                {"applied_count": len(applied), "fact_refs": [str(row["id"]) for row in applied]},
            )
        if self._shots(episode_id):
            return self._report(
                "SKIPPED",
                "EXISTING_EPISODE_PLAN_AUTHORITY",
                "该集已有镜头计划；未自动生成或应用剧本拆解草稿",
                {"fact_refs": [str(row["id"]) for row in self._shots(episode_id)]},
            )
        source_report, _ = self.story_parse(episode_id)
        if str(source_report["machine_check"]["status"]) == "NEEDS_HITL":
            return source_report, 0
        ready = self._ready_breakdowns(project_id)
        if ready:
            return self._report(
                "NEEDS_HITL",
                "BREAKDOWN_REVIEW_APPLY_REQUIRED",
                "剧本拆解草稿已就绪，但必须由人工复核并应用到目标分集",
                {"draft_count": len(ready), "fact_refs": [str(row["id"]) for row in ready]},
            )
        return self._report(
            "NEEDS_HITL",
            "BREAKDOWN_DRAFT_REQUIRED",
            "剧本源已提交，但尚无可审核的拆解草稿；请发起可恢复的拆解任务",
            {"project_id": project_id, "fact_refs": []},
        )

    def _episode_proposals(self, project_id: str, episode_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT p.*,a.status AS resolved_asset_status,a.kind AS resolved_asset_kind
                FROM story_asset_proposals p
                LEFT JOIN story_assets a ON a.id=p.resolved_asset_id
                WHERE p.breakdown_draft_id IN (
                  SELECT d.id FROM script_breakdown_drafts d
                  WHERE d.project_id=? AND d.status='APPLIED' AND EXISTS (
                    SELECT 1 FROM audit_events ae WHERE ae.action='SCRIPT_BREAKDOWN_APPLIED'
                    AND ae.subject_id=d.id
                    AND json_extract(ae.metadata_redacted_json,'$.episode_id')=?
                  )
                ) ORDER BY p.created_at,p.id""",
                (project_id, episode_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def asset_identity(self, episode_id: str) -> tuple[dict[str, Any], int]:
        episode = self._episode(episode_id)
        project_id = str(episode["project_id"])
        applied = self._applied_breakdowns(project_id, episode_id)
        if not applied:
            if self._shots(episode_id):
                return self._report(
                    "SKIPPED",
                    "EXISTING_EPISODE_PLAN_AUTHORITY",
                    "该集使用既有镜头/资产事实；没有自动推断或合并角色身份",
                    {"fact_refs": [str(row["id"]) for row in self._shots(episode_id)]},
                )
            return self._report(
                "NEEDS_HITL",
                "BREAKDOWN_APPLY_REQUIRED",
                "资产身份核对前必须先由人工应用剧本拆解草稿",
                {"fact_refs": []},
            )
        proposals = self._episode_proposals(project_id, episode_id)
        pending = [row for row in proposals if str(row["status"]) == "PENDING"]
        invalid = [
            row for row in proposals
            if str(row["status"]) in {"ACCEPTED_NEW", "ACCEPTED_MERGE"}
            and (
                not row.get("resolved_asset_id")
                or str(row.get("resolved_asset_status") or "") != "ACTIVE"
                or str(row.get("resolved_asset_kind") or "") != str(row.get("kind") or "")
            )
        ]
        if pending or invalid:
            return self._report(
                "NEEDS_HITL",
                "ASSET_IDENTITY_DECISION_REQUIRED",
                "仍有资产身份建议需要人工创建、合并或拒绝",
                {
                    "proposal_count": len(proposals),
                    "pending_proposal_ids": [str(row["id"]) for row in pending],
                    "invalid_resolution_ids": [str(row["id"]) for row in invalid],
                    "fact_refs": [str(row["id"]) for row in proposals],
                },
            )
        return self._report(
            "PASS",
            "ASSET_IDENTITIES_RESOLVED",
            "本集拆解产生的资产身份建议已全部由人工处理",
            {
                "proposal_count": len(proposals),
                "accepted_count": sum(str(row["status"]).startswith("ACCEPTED_") for row in proposals),
                "rejected_count": sum(str(row["status"]) == "REJECTED" for row in proposals),
                "fact_refs": [str(row["id"]) for row in proposals],
            },
        )

    @staticmethod
    def _complete_pack(version: dict[str, Any], slots: list[dict[str, Any]]) -> tuple[bool, list[str]]:
        available = {
            str(row["slot_kind"]).upper()
            for row in slots
            if str(row.get("integrity_status") or "") == "VERIFIED"
        }
        missing = sorted(set(REQUIRED_THREE_VIEW_SLOTS) - available)
        return str(version.get("status") or "") == "APPROVED" and not missing, missing

    def asset_completion(self, episode_id: str) -> tuple[dict[str, Any], int]:
        episode = self._episode(episode_id)
        project_id = str(episode["project_id"])
        identity_report, _ = self.asset_identity(episode_id)
        if str(identity_report["machine_check"]["status"]) == "NEEDS_HITL":
            return identity_report, 0
        proposals = self._episode_proposals(project_id, episode_id)
        asset_ids = {
            str(row["resolved_asset_id"])
            for row in proposals
            if str(row["status"]).startswith("ACCEPTED_") and row.get("resolved_asset_id")
        }
        with self.database.connect() as connection:
            bindings = connection.execute(
                """SELECT sab.shot_id,sab.asset_id,sab.identity_pack_version_id,
                sa.code AS asset_code,sa.kind,s.code AS shot_code
                FROM shot_asset_bindings sab JOIN shots s ON s.id=sab.shot_id
                JOIN story_assets sa ON sa.id=sab.asset_id
                WHERE s.episode_id=? AND s.archived_at IS NULL AND sa.kind='CHARACTER'
                ORDER BY s.code,sa.code""",
                (episode_id,),
            ).fetchall()
            asset_ids.update(str(row["asset_id"]) for row in bindings)
            versions = connection.execute(
                """SELECT v.*,p.current_version_id FROM character_identity_pack_versions v
                JOIN character_identity_packs p ON p.id=v.pack_id
                WHERE v.story_asset_id IN ({}) AND p.status='ACTIVE'
                ORDER BY v.story_asset_id,v.version_no DESC""".format(
                    ",".join("?" for _ in asset_ids)
                ),
                tuple(sorted(asset_ids)),
            ).fetchall() if asset_ids else []
            slots = connection.execute(
                """SELECT s.pack_version_id,s.slot_kind,mv.integrity_status
                FROM character_identity_pack_slots s
                JOIN media_versions mv ON mv.id=s.media_version_id
                WHERE s.pack_version_id IN ({})""".format(
                    ",".join("?" for _ in versions)
                ),
                tuple(str(row["id"]) for row in versions),
            ).fetchall() if versions else []
        slots_by_version: dict[str, list[dict[str, Any]]] = {}
        for row in slots:
            slots_by_version.setdefault(str(row["pack_version_id"]), []).append(dict(row))
        complete_by_asset: dict[str, set[str]] = {}
        incomplete_versions: list[dict[str, Any]] = []
        for raw in versions:
            version = dict(raw)
            complete, missing = self._complete_pack(version, slots_by_version.get(str(version["id"]), []))
            if complete and str(version["current_version_id"] or "") == str(version["id"]):
                complete_by_asset.setdefault(str(version["story_asset_id"]), set()).add(str(version["id"]))
            elif str(version["status"]) in {"DRAFT", "APPROVED"}:
                incomplete_versions.append(
                    {"pack_version_id": str(version["id"]), "asset_id": str(version["story_asset_id"]), "status": str(version["status"]), "missing_slots": missing}
                )
        missing_assets = sorted(asset_id for asset_id in asset_ids if not complete_by_asset.get(asset_id))
        invalid_bindings: list[dict[str, Any]] = []
        for raw in bindings:
            row = dict(raw)
            version_id = str(row.get("identity_pack_version_id") or "")
            if not version_id or version_id not in complete_by_asset.get(str(row["asset_id"]), set()):
                invalid_bindings.append(
                    {
                        "shot_id": str(row["shot_id"]),
                        "shot_code": str(row["shot_code"]),
                        "asset_id": str(row["asset_id"]),
                        "asset_code": str(row["asset_code"]),
                        "identity_pack_version_id": version_id or None,
                    }
                )
        if missing_assets or invalid_bindings:
            return self._report(
                "NEEDS_HITL",
                "ASSET_COMPLETION_REQUIRED",
                "角色资产仍缺少已人工批准的三视图身份包，或镜头尚未绑定生效版本",
                {
                    "required_slots": sorted(REQUIRED_THREE_VIEW_SLOTS),
                    "missing_asset_ids": missing_assets,
                    "invalid_shot_bindings": invalid_bindings,
                    "incomplete_versions": incomplete_versions,
                    "fact_refs": sorted(asset_ids),
                },
            )
        return self._report(
            "PASS",
            "ASSET_COMPLETION_VERIFIED",
            "本集涉及的角色资产已具备并绑定人工批准的三视图身份包",
            {
                "character_asset_count": len(asset_ids),
                "bound_character_count": len(bindings),
                "required_slots": sorted(REQUIRED_THREE_VIEW_SLOTS),
                "fact_refs": sorted({version_id for values in complete_by_asset.values() for version_id in values}),
            },
        )

    def episode_plan(self, episode_id: str) -> tuple[dict[str, Any], int]:
        shots = self._shots(episode_id)
        if not shots:
            return self._report(
                "NEEDS_HITL",
                "EPISODE_PLAN_REQUIRED",
                "该集尚无镜头计划；必须先人工应用/编辑并确认分镜",
                {"shot_count": 0, "invalid_shots": [], "fact_refs": []},
            )
        invalid: list[dict[str, Any]] = []
        for shot in shots:
            fields = _decode(shot.get("fields_json"), {})
            fields = fields if isinstance(fields, dict) else {}
            missing = missing_shot_fields(fields)
            if not shot.get("current_revision_id") or missing or str(shot["status"]) not in {"READY", "GENERATING", "REVIEW", "APPROVED"}:
                invalid.append(
                    {
                        "shot_id": str(shot["id"]),
                        "shot_code": str(shot["code"]),
                        "status": str(shot["status"]),
                        "current_revision_id": shot.get("current_revision_id"),
                        "missing_fields": missing,
                    }
                )
        if invalid:
            return self._report(
                "NEEDS_HITL",
                "EPISODE_PLAN_REVIEW_REQUIRED",
                "部分镜头尚未达到 production-ready；保留原 revision，等待人工补齐",
                {"shot_count": len(shots), "invalid_shots": invalid, "fact_refs": [str(row["id"]) for row in shots]},
            )
        return self._report(
            "PASS",
            "EPISODE_PLAN_VERIFIED",
            "本集所有镜头均引用完整且可生产的 revision",
            {"shot_count": len(shots), "invalid_shots": [], "fact_refs": [str(row["current_revision_id"]) for row in shots]},
        )

    def keyframe_check(self, episode_id: str) -> tuple[dict[str, Any], int]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT s.id,s.code,EXISTS(
                  SELECT 1 FROM media_assets ma
                  JOIN media_versions mv ON mv.id=ma.approved_version_id
                  WHERE ma.owner_type='SHOT' AND ma.owner_id=s.id
                  AND ma.purpose='KEYFRAME' AND ma.media_kind='IMAGE'
                  AND mv.stage='KEYFRAME' AND mv.integrity_status='VERIFIED'
                  AND EXISTS (
                    SELECT 1 FROM review_decisions rd
                    WHERE rd.subject_type='MEDIA_VERSION' AND rd.subject_id=mv.id
                    AND rd.decision='APPROVED' AND rd.is_stale=0
                  )
                ) AS approved
                FROM shots s WHERE s.episode_id=? AND s.archived_at IS NULL
                ORDER BY CAST(s.order_key AS REAL),s.code""",
                (episode_id,),
            ).fetchall()
        shots = [dict(row) for row in rows]
        missing = [
            {"shot_id": str(shot["id"]), "shot_code": str(shot["code"])}
            for shot in shots if not bool(shot["approved"])
        ]
        if not shots or missing:
            return self._report(
                "NEEDS_HITL",
                "APPROVED_KEYFRAME_REQUIRED",
                "镜头缺少未过期的人工批准关键帧" if shots else "该集尚无可检查的镜头",
                {"checked_shots": len(shots), "missing_shots": missing, "fact_refs": [str(row["id"]) for row in shots]},
            )
        return self._report(
            "PASS",
            "APPROVED_KEYFRAMES_VERIFIED",
            "全部镜头关键帧均有未过期的人工批准证据",
            {"checked_shots": len(shots), "missing_shots": [], "fact_refs": [str(row["id"]) for row in shots]},
        )

    def run(self, action: str, episode_id: str) -> tuple[dict[str, Any], int]:
        normalized = str(action or "").strip().upper()
        handlers = {
            "STORY_PARSE": self.story_parse,
            "SCRIPT_BREAKDOWN": self.script_breakdown,
            "ASSET_IDENTITY": self.asset_identity,
            "ASSET_COMPLETION": self.asset_completion,
            "EPISODE_PLAN": self.episode_plan,
            "KEYFRAME_CHECK": self.keyframe_check,
        }
        handler = handlers.get(normalized)
        if handler is None:
            raise DomainRuleError("AUTOMATION_ACTION_UNSUPPORTED", "不支持的前半链路 action", {"action": normalized})
        return handler(episode_id)

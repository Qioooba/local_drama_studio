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

from .episode_source_binding import resolve_episode_source_binding
from .keyframe_references import approved_keyframes_for_shots
from .production_asset_inputs import ProductionAssetInputService
from .production_identity_inputs import (
    ProductionIdentityHeroPreparationService,
    ProductionIdentityInputService,
    ProductionIdentityPreparationService,
)


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
            "ASSET_HERO_COMPLETION",
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

    def _committed_source(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            return resolve_episode_source_binding(connection, episode_id)

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

    def _applied_breakdowns(
        self, project_id: str, episode_id: str, source: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT d.id,d.source_document_version_id,d.import_session_id,d.status,d.revision
                FROM script_breakdown_drafts d
                WHERE d.project_id=? AND d.status='APPLIED' AND EXISTS (
                  SELECT 1 FROM audit_events ae
                  WHERE ae.action='SCRIPT_BREAKDOWN_APPLIED'
                  AND ae.subject_type='script_breakdown_draft' AND ae.subject_id=d.id
                  AND json_extract(ae.metadata_redacted_json,'$.episode_id')=?
                ) AND (? IS NULL OR d.source_document_version_id=?)
                AND (? IS NULL OR d.import_session_id=?)
                ORDER BY d.updated_at DESC,d.id DESC""",
                (
                    project_id,
                    episode_id,
                    source.get("source_document_version_id") if source else None,
                    source.get("source_document_version_id") if source else None,
                    source.get("import_session_id") if source else None,
                    source.get("import_session_id") if source else None,
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def _ready_breakdowns(
        self, project_id: str, episode_id: str, source: dict[str, Any]
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT id,source_document_version_id,import_session_id,status,revision
                FROM script_breakdown_drafts WHERE project_id=? AND status='DRAFT_READY'
                AND source_document_version_id=? AND import_session_id=?
                AND json_extract(confidence_json,'$.target_episode_id')=?
                ORDER BY updated_at DESC,id DESC""",
                (project_id, source["source_document_version_id"], source["import_session_id"], episode_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def snapshot(self, episode_id: str) -> dict[str, Any]:
        """Return a bounded fingerprint input for recovery/stale detection."""
        episode = self._episode(episode_id)
        project_id = str(episode["project_id"])
        source_error: DomainRuleError | None = None
        try:
            source = self._committed_source(episode_id)
        except DomainRuleError as error:
            source = None
            source_error = error
        applied = self._applied_breakdowns(project_id, episode_id, source) if source else []
        source_version_id = (
            str(source["source_document_version_id"]) if source else "__UNRESOLVED__"
        )
        import_session_id = str(source["import_session_id"]) if source else "__UNRESOLVED__"
        with self.database.connect() as connection:
            drafts = connection.execute(
                """SELECT id,status,revision,source_document_version_id,import_session_id
                FROM script_breakdown_drafts WHERE project_id=?
                AND json_extract(confidence_json,'$.target_episode_id')=?
                AND source_document_version_id=? AND import_session_id=?
                ORDER BY 1""",
                (
                    project_id,
                    episode_id,
                    source_version_id,
                    import_session_id,
                ),
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
                  AND d.source_document_version_id=? AND d.import_session_id=?
                ) ORDER BY p.id""",
                (project_id, episode_id, source_version_id, import_session_id),
            ).fetchall()
            packs = connection.execute(
                """SELECT DISTINCT v.id,v.status,v.revision,p.current_version_id
                FROM character_identity_packs p
                JOIN character_identity_pack_versions v ON v.id=p.current_version_id
                WHERE p.story_asset_id IN (
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
                    AND d.source_document_version_id=? AND d.import_session_id=?
                  )
                )
                UNION
                SELECT DISTINCT selected.id,selected.status,selected.revision,p.current_version_id
                FROM shot_asset_bindings sab
                JOIN shots s ON s.id=sab.shot_id
                JOIN character_identity_pack_versions selected ON selected.id=sab.identity_pack_version_id
                JOIN character_identity_packs p ON p.id=selected.pack_id
                WHERE s.episode_id=? AND s.archived_at IS NULL""",
                (
                    episode_id,
                    project_id,
                    episode_id,
                    source_version_id,
                    import_session_id,
                    episode_id,
                ),
            ).fetchall()
            packs = sorted(packs, key=lambda row: str(row["id"]))
            identity_bindings = connection.execute(
                """SELECT sab.shot_id,sab.asset_id,sab.identity_pack_version_id,sab.revision
                FROM shot_asset_bindings sab JOIN shots s ON s.id=sab.shot_id
                JOIN story_assets sa ON sa.id=sab.asset_id
                WHERE s.episode_id=? AND s.archived_at IS NULL AND sa.kind='CHARACTER'
                ORDER BY sab.shot_id,sab.asset_id,sab.role_in_shot""",
                (episode_id,),
            ).fetchall()
            episode_shots = connection.execute(
                """SELECT s.id FROM shots s
                WHERE s.episode_id=? AND s.archived_at IS NULL
                ORDER BY CAST(s.order_key AS REAL),s.code""",
                (episode_id,),
            ).fetchall()
            approved_keyframes = approved_keyframes_for_shots(
                connection,
                (str(row["id"]) for row in episode_shots),
                project_id=project_id,
            )
            keyframes = [
                {
                    "shot_id": str(row["id"]),
                    "approved_version_id": approved_keyframes.get(str(row["id"]), {}).get("media_version_id"),
                    "review_decision_id": approved_keyframes.get(str(row["id"]), {}).get("review_decision_id"),
                    "review_revision": approved_keyframes.get(str(row["id"]), {}).get("review_revision"),
                    "is_stale": approved_keyframes.get(str(row["id"]), {}).get("is_stale"),
                    "owner_type": approved_keyframes.get(str(row["id"]), {}).get("owner_type"),
                    "owner_id": approved_keyframes.get(str(row["id"]), {}).get("owner_id"),
                }
                for row in episode_shots
            ]
        return {
            "source": {
                "import_session_id": source.get("import_session_id"),
                "source_document_version_id": source.get("source_document_version_id"),
                "text_sha256": source.get("text_sha256"),
                "parse_status": source.get("parse_status"),
            } if source else None,
            "source_error": {
                "code": source_error.code,
                "details": source_error.details or {},
            } if source_error else None,
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
                    "current_revision_id": row["current_revision_id"],
                }
                for row in self._shots(episode_id)
            ],
        }

    def story_parse(self, episode_id: str) -> tuple[dict[str, Any], int]:
        episode = self._episode(episode_id)
        try:
            source = self._committed_source(episode_id)
        except DomainRuleError as error:
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
                error.code,
                error.message,
                {"project_id": str(episode["project_id"]), **(error.details or {}), "fact_refs": []},
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
        try:
            source = self._committed_source(episode_id)
        except DomainRuleError:
            source = None
        applied = self._applied_breakdowns(project_id, episode_id, source) if source else []
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
        source = self._committed_source(episode_id)
        ready = self._ready_breakdowns(project_id, episode_id, source)
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

    def session_asset_completion(
        self, production_session_id: str, episode_id: str
    ) -> tuple[dict[str, Any], int]:
        """Prepare verified draft identities without creating approval facts."""

        ProductionIdentityHeroPreparationService(
            self.database, self.settings
        ).reconcile_completed(production_session_id, episode_id)
        formal_report, produced = self.asset_completion(episode_id)
        if str(formal_report["machine_check"]["status"]) in {"PASS", "SKIPPED"}:
            return formal_report, produced
        result = ProductionIdentityInputService(self.database).ensure_episode_inputs(
            production_session_id,
            episode_id,
            actor="production-session-worker",
        )
        if not result["ready"]:
            preparation = ProductionIdentityPreparationService(
                self.database, self.settings
            ).dispatch_episode(
                production_session_id,
                episode_id,
                actor="production-session-worker",
            )
            refreshed = ProductionIdentityInputService(
                self.database
            ).ensure_episode_inputs(
                production_session_id,
                episode_id,
                actor="production-session-worker",
            )
            if refreshed["ready"]:
                return self._report(
                    "PASS",
                    "PRODUCTION_SESSION_IDENTITY_INPUTS_READY",
                    "当前会话人物三视图已登记并冻结为机器临时输入",
                    {
                        "production_session_id": production_session_id,
                        "selection_authority": "MACHINE_TEMPORARY",
                        "human_approval_created": False,
                        "fact_refs": [
                            str(item["id"]) for item in refreshed["registered"]
                        ],
                    },
                )
            if preparation["ready"] and preparation["dependency_job_ids"]:
                dependency_items = [
                    {"job_id": job_id}
                    for job_id in preparation["dependency_job_ids"]
                ]
                return (
                    {
                        "status": "PASS",
                        "machine_check": {
                            "status": "PASS",
                            "ok": True,
                            "code": "PRODUCTION_SESSION_IDENTITY_GENERATION_DISPATCHED",
                            "detail": "已提交当前会话缺失的真实人物三视图生成任务",
                            "human_approval_created": False,
                            "submitted_count": len(preparation["submitted"]),
                        },
                        "produced": {
                            "items": dependency_items,
                            "submissions": preparation["submitted"],
                            "writes": ["production_session_job_links"],
                        },
                        "summary": "人物三视图已投放；下游任务等待真实产物登记完成",
                    },
                    0,
                )
            return self._report(
                "NEEDS_HITL",
                "PRODUCTION_SESSION_IDENTITY_INPUT_REQUIRED",
                "当前会话仍有角色缺少完整、已验证且已授权的三视图草稿",
                {
                    "production_session_id": production_session_id,
                    "missing": result["missing"],
                    "candidate_failures": result["candidate_failures"],
                    "preparation_blockers": preparation["blockers"],
                    "human_approval_created": False,
                    "fact_refs": [str(item["id"]) for item in result["registered"]],
                },
            )
        return self._report(
            "PASS",
            "PRODUCTION_SESSION_IDENTITY_INPUTS_READY",
            "当前会话所需人物身份输入已冻结；草稿仍未被标记为人工批准",
            {
                "production_session_id": production_session_id,
                "selection_authority": "MACHINE_TEMPORARY",
                "human_approval_created": False,
                "registered_count": len(result["registered"]),
                "fact_refs": [str(item["id"]) for item in result["registered"]],
            },
        )

    def session_asset_identity(
        self, production_session_id: str, episode_id: str
    ) -> tuple[dict[str, Any], int]:
        result = ProductionAssetInputService(self.database).ensure_episode_inputs(
            production_session_id,
            episode_id,
            actor="production-session-worker",
        )
        if not result["ready"]:
            return self._report(
                "NEEDS_HITL",
                "PRODUCTION_SESSION_ASSET_IDENTITY_REVIEW_REQUIRED",
                "资产身份存在歧义或无效输入，需要人工核对",
                {
                    "production_session_id": production_session_id,
                    "blockers": result["blockers"],
                    "human_approval_created": False,
                    "fact_refs": [],
                },
            )
        temporary = [
            item
            for item in result["items"]
            if item["status"] == "SESSION_READY"
        ]
        return self._report(
            "PASS",
            "PRODUCTION_SESSION_ASSET_IDENTITIES_READY",
            "资产身份已按精确匹配登记为本次会话机器临时输入；原建议仍等待人工决定",
            {
                "production_session_id": production_session_id,
                "selection_authority": "MACHINE_TEMPORARY" if temporary else "HUMAN_RESOLVED",
                "human_approval_created": False,
                "temporary_count": len(temporary),
                "fact_refs": [str(item["input_id"]) for item in temporary],
            },
        )

    def session_asset_hero_completion(
        self, production_session_id: str, episode_id: str
    ) -> tuple[dict[str, Any], int]:
        """Generate missing bound-asset HERO inputs before multi-view preparation."""

        preparation = ProductionIdentityHeroPreparationService(
            self.database, self.settings
        ).dispatch_episode(
            production_session_id,
            episode_id,
            actor="production-session-worker",
        )
        if not preparation["ready"]:
            return self._report(
                "NEEDS_HITL",
                "PRODUCTION_SESSION_HERO_INPUT_REQUIRED",
                "当前会话仍有关键资产缺少可自动生成的主图",
                {
                    "production_session_id": production_session_id,
                    "preparation_blockers": preparation["blockers"],
                    "human_approval_created": False,
                    "fact_refs": [],
                },
            )
        dependency_job_ids = list(preparation["dependency_job_ids"])
        if dependency_job_ids:
            return (
                {
                    "status": "PASS",
                    "machine_check": {
                        "status": "PASS",
                        "ok": True,
                        "code": "PRODUCTION_SESSION_HERO_GENERATION_DISPATCHED",
                        "detail": "已按资产类型提交当前会话缺失的主图生成任务",
                        "human_approval_created": False,
                        "submitted_count": len(preparation["submitted"]),
                    },
                    "produced": {
                        "items": [
                            {"job_id": job_id} for job_id in dependency_job_ids
                        ],
                        "submissions": preparation["submitted"],
                        "writes": ["production_session_job_links"],
                    },
                    "summary": "关键资产主图已投放；后续阶段等待真实 HERO 登记完成",
                },
                0,
            )
        return self._report(
            "PASS",
            "PRODUCTION_SESSION_HERO_INPUTS_READY",
            "当前会话所需关键资产主图已具备，可继续准备人物三视图",
            {
                "production_session_id": production_session_id,
                "human_approval_created": False,
                "fact_refs": [
                    str(item["asset_id"]) for item in preparation["items"]
                ],
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

    def session_episode_plan(
        self, production_session_id: str, episode_id: str
    ) -> tuple[dict[str, Any], int]:
        report, produced = self.episode_plan(episode_id)
        if str(report["machine_check"]["status"]) in {"PASS", "SKIPPED"}:
            return report, produced
        shots = self._shots(episode_id)
        with self.database.connect() as connection:
            applied_shot_ids = {
                str(row["id"])
                for row in connection.execute(
                    """SELECT sh.id FROM shots sh
                       JOIN script_breakdown_scene_applications app
                         ON app.created_scene_id=sh.scene_id AND app.episode_id=sh.episode_id
                       JOIN script_breakdown_drafts d ON d.id=app.breakdown_draft_id
                       WHERE sh.episode_id=? AND sh.archived_at IS NULL
                         AND d.status='APPLIED'""",
                    (episode_id,),
                ).fetchall()
            }
        invalid: list[dict[str, Any]] = []
        for shot in shots:
            fields = _decode(shot.get("fields_json"), {})
            fields = fields if isinstance(fields, dict) else {}
            missing = missing_shot_fields(fields)
            if (
                not shot.get("current_revision_id")
                or missing
                or str(shot["id"]) not in applied_shot_ids
                or str(shot["status"])
                not in {"DRAFT", "READY", "GENERATING", "REVIEW", "APPROVED"}
            ):
                invalid.append(
                    {
                        "shot_id": str(shot["id"]),
                        "shot_code": str(shot["code"]),
                        "status": str(shot["status"]),
                        "missing_fields": missing,
                    }
                )
        if invalid or not shots:
            return self._report(
                "NEEDS_HITL",
                "PRODUCTION_SESSION_EPISODE_PLAN_REVIEW_REQUIRED",
                "自动应用的分镜仍有缺项或无法追溯到本集拆解草稿",
                {
                    "production_session_id": production_session_id,
                    "invalid_shots": invalid,
                    "human_approval_created": False,
                    "fact_refs": [str(row["id"]) for row in shots],
                },
            )
        return self._report(
            "PASS",
            "PRODUCTION_SESSION_EPISODE_PLAN_READY",
            "自动应用的完整分镜已登记为本次会话机器临时输入，原镜头状态未冒充人工确认",
            {
                "production_session_id": production_session_id,
                "selection_authority": "MACHINE_TEMPORARY",
                "human_approval_created": False,
                "shot_count": len(shots),
                "fact_refs": [str(row["current_revision_id"]) for row in shots],
            },
        )

    def keyframe_check(self, episode_id: str) -> tuple[dict[str, Any], int]:
        episode = self._episode(episode_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT s.id,s.code
                FROM shots s WHERE s.episode_id=? AND s.archived_at IS NULL
                ORDER BY CAST(s.order_key AS REAL),s.code""",
                (episode_id,),
            ).fetchall()
            approved = approved_keyframes_for_shots(
                connection,
                (str(row["id"]) for row in rows),
                project_id=str(episode["project_id"]),
            )
        shots = [dict(row) for row in rows]
        missing = [
            {"shot_id": str(shot["id"]), "shot_code": str(shot["code"])}
            for shot in shots if str(shot["id"]) not in approved
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

    def run(
        self,
        action: str,
        episode_id: str,
        *,
        production_session_id: str | None = None,
    ) -> tuple[dict[str, Any], int]:
        normalized = str(action or "").strip().upper()
        if normalized == "ASSET_IDENTITY" and production_session_id:
            return self.session_asset_identity(production_session_id, episode_id)
        if normalized == "ASSET_HERO_COMPLETION" and production_session_id:
            return self.session_asset_hero_completion(
                production_session_id, episode_id
            )
        if normalized == "ASSET_COMPLETION" and production_session_id:
            return self.session_asset_completion(production_session_id, episode_id)
        if normalized == "EPISODE_PLAN" and production_session_id:
            return self.session_episode_plan(production_session_id, episode_id)
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

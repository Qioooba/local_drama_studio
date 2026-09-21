"""Session-scoped asset identity mappings for unattended story preparation.

The mapping lets a production session use an exact existing asset or a newly
created provisional asset while the original Story Asset Proposal remains
PENDING for a person.  It never records ACCEPTED_NEW / ACCEPTED_MERGE and never
pretends that a machine match was a human identity decision.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.application.breakdown_apply import BreakdownApplyService
from local_drama.application.breakdown_contracts import normalized_entity_name
from local_drama.application.breakdown_revisions import load_effective_breakdown_draft
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.story_entities import assess_entity_name


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    import hashlib

    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class ProductionAssetInputService:
    """Plan and register non-destructive, session-only asset mappings."""

    def __init__(self, database: DatabaseUnitOfWork) -> None:
        self.database = database

    @staticmethod
    def _session(
        connection: sqlite3.Connection, session_id: str, episode_id: str
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        session = connection.execute(
            "SELECT * FROM production_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if session is None:
            raise DomainRuleError("PRODUCTION_SESSION_NOT_FOUND", "生产会话不存在")
        if str(session["status"]) in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise DomainRuleError(
                "PRODUCTION_SESSION_ASSET_INPUT_INACTIVE",
                "已结束的生产会话不能继续创建临时资产输入",
            )
        item = connection.execute(
            "SELECT * FROM production_session_items WHERE session_id=? AND episode_id=?",
            (session_id, episode_id),
        ).fetchone()
        if item is None:
            raise DomainRuleError(
                "PRODUCTION_SESSION_EPISODE_OUT_OF_SCOPE", "分集不属于当前生产会话"
            )
        return session, item

    @staticmethod
    def _proposals(
        connection: sqlite3.Connection, project_id: str, episode_id: str
    ) -> list[sqlite3.Row]:
        return connection.execute(
            """SELECT p.*,resolved.status AS resolved_asset_status,
                      resolved.kind AS resolved_asset_kind
               FROM story_asset_proposals p
               LEFT JOIN story_assets resolved ON resolved.id=p.resolved_asset_id
               WHERE p.breakdown_draft_id IN (
                 SELECT d.id FROM script_breakdown_drafts d
                 WHERE d.project_id=? AND d.status='APPLIED' AND EXISTS (
                   SELECT 1 FROM audit_events ae
                   WHERE ae.action='SCRIPT_BREAKDOWN_APPLIED' AND ae.subject_id=d.id
                     AND json_extract(ae.metadata_redacted_json,'$.episode_id')=?
                 )
               ) ORDER BY p.created_at,p.id""",
            (project_id, episode_id),
        ).fetchall()

    @staticmethod
    def _asset_identity_names(asset: dict[str, Any] | sqlite3.Row) -> set[str]:
        names = {normalized_entity_name(asset["name"])}
        try:
            extra = json.loads(str(asset["extra_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            extra = {}
        dossier = extra.get("text_dossier") if isinstance(extra, dict) else None
        if isinstance(dossier, dict):
            names.update(
                normalized_entity_name(alias)
                for alias in (dossier.get("aliases") or [])
                if normalized_entity_name(alias)
            )
        return {name for name in names if name}

    @staticmethod
    def _asset_matches(
        connection: sqlite3.Connection,
        *,
        project_id: str,
        kind: str,
        name: str,
    ) -> list[dict[str, Any]]:
        target = normalized_entity_name(name)
        rows = connection.execute(
            """SELECT id,project_id,kind,code,name,status,revision,extra_json
               FROM story_assets WHERE project_id=? AND kind=? AND status='ACTIVE'
               ORDER BY created_at,id""",
            (project_id, kind),
        ).fetchall()
        return [
            dict(row)
            for row in rows
            if target in ProductionAssetInputService._asset_identity_names(row)
        ]

    def plan_episode(self, session_id: str, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            session, item = self._session(connection, session_id, episode_id)
            project_id = str(session["project_id"])
            proposals = self._proposals(connection, project_id, episode_id)
            inputs = {
                str(row["asset_proposal_id"]): dict(row)
                for row in connection.execute(
                    """SELECT i.*,a.status AS asset_status,a.kind AS asset_kind,
                              a.revision AS asset_revision
                       FROM production_session_asset_inputs i
                       JOIN story_assets a ON a.id=i.story_asset_id
                       WHERE i.session_id=? AND i.episode_id=? AND i.state='ACTIVE'""",
                    (session_id, episode_id),
                ).fetchall()
            }
            items: list[dict[str, Any]] = []
            blockers: list[dict[str, Any]] = []
            for row in proposals:
                proposal = dict(row)
                proposal_id = str(proposal["id"])
                status = str(proposal["status"])
                planned: dict[str, Any] = {
                    "proposal_id": proposal_id,
                    "proposal_revision": int(proposal["revision"]),
                    "kind": str(proposal["kind"]),
                    "name": str(proposal["name"]),
                }
                if status in {"ACCEPTED_NEW", "ACCEPTED_MERGE"}:
                    valid = bool(
                        proposal.get("resolved_asset_id")
                        and str(proposal.get("resolved_asset_status") or "") == "ACTIVE"
                        and str(proposal.get("resolved_asset_kind") or "")
                        == str(proposal["kind"])
                    )
                    planned.update(
                        {
                            "status": "FORMAL" if valid else "BLOCKED",
                            "asset_id": proposal.get("resolved_asset_id"),
                            "blockers": (
                                []
                                if valid
                                else [
                                    {
                                        "code": "ASSET_PROPOSAL_RESOLUTION_INVALID",
                                        "message": "已处理的资产建议没有有效的同类型资产",
                                    }
                                ]
                            ),
                        }
                    )
                elif status == "REJECTED":
                    planned.update({"status": "SKIPPED", "asset_id": None, "blockers": []})
                elif status != "PENDING":
                    planned.update(
                        {
                            "status": "BLOCKED",
                            "asset_id": None,
                            "blockers": [
                                {
                                    "code": "ASSET_PROPOSAL_STATUS_INVALID",
                                    "message": "资产建议状态不可用于生产",
                                }
                            ],
                        }
                    )
                elif proposal_id in inputs:
                    existing = inputs[proposal_id]
                    valid = bool(
                        str(existing["asset_status"]) == "ACTIVE"
                        and str(existing["asset_kind"]) == str(proposal["kind"])
                    )
                    planned.update(
                        {
                            "status": "SESSION_READY" if valid else "BLOCKED",
                            "asset_id": str(existing["story_asset_id"]),
                            "input_id": str(existing["id"]),
                            "blockers": (
                                []
                                if valid
                                else [
                                    {
                                        "code": "PRODUCTION_SESSION_ASSET_INPUT_STALE",
                                        "message": "会话临时资产已归档或类型发生变化",
                                    }
                                ]
                            ),
                        }
                    )
                else:
                    assessment = assess_entity_name(
                        str(proposal["kind"]), proposal["name"]
                    )
                    matches = self._asset_matches(
                        connection,
                        project_id=project_id,
                        kind=str(proposal["kind"]),
                        name=str(proposal["name"]),
                    )
                    suggested = None
                    if proposal.get("suggested_asset_id"):
                        candidate = connection.execute(
                            """SELECT * FROM story_assets
                               WHERE id=? AND project_id=? AND kind=? AND status='ACTIVE'""",
                            (
                                proposal["suggested_asset_id"],
                                project_id,
                                proposal["kind"],
                            ),
                        ).fetchone()
                        if candidate is not None and normalized_entity_name(
                            proposal["name"]
                        ) in self._asset_identity_names(candidate):
                            suggested = dict(candidate)
                    unique = {str(match["id"]): match for match in matches}
                    if suggested is not None:
                        unique[str(suggested["id"])] = suggested
                    if not assessment.valid:
                        planned.update(
                            {
                                "status": "BLOCKED",
                                "asset_id": None,
                                "blockers": [
                                    {
                                        "code": "ASSET_PROPOSAL_NAME_REVIEW_REQUIRED",
                                        "message": assessment.reason
                                        or "资产名称需要人工核对",
                                    }
                                ],
                            }
                        )
                    elif len(unique) > 1:
                        planned.update(
                            {
                                "status": "BLOCKED",
                                "asset_id": None,
                                "blockers": [
                                    {
                                        "code": "ASSET_PROPOSAL_MATCH_AMBIGUOUS",
                                        "message": "存在多个同名有效资产，不能自动合并",
                                        "candidate_asset_ids": sorted(unique),
                                    }
                                ],
                            }
                        )
                    elif unique:
                        planned.update(
                            {
                                "status": "READY_TO_BIND",
                                "asset_id": next(iter(unique)),
                                "blockers": [],
                            }
                        )
                    else:
                        planned.update(
                            {
                                "status": "READY_TO_CREATE",
                                "asset_id": None,
                                "blockers": [],
                            }
                        )
                items.append(planned)
                blockers.extend(
                    {
                        **blocker,
                        "proposal_id": proposal_id,
                        "name": str(proposal["name"]),
                    }
                    for blocker in planned.get("blockers", [])
                )
        return {
            "session_id": session_id,
            "session_item_id": str(item["id"]),
            "episode_id": episode_id,
            "project_id": project_id,
            "items": items,
            "blockers": blockers,
            "ready": not blockers,
            "mutated": False,
            "runtime_contacted": False,
            "network_contacted": False,
        }

    @staticmethod
    def _provisional_code(proposal_id: str, kind: str) -> str:
        prefix = {
            "CHARACTER": "TMP_CHAR",
            "SCENE": "TMP_SCENE",
            "PROP": "TMP_PROP",
            "COSTUME": "TMP_COSTUME",
        }.get(kind, "TMP_ASSET")
        token = re.sub(r"[^A-F0-9]", "", proposal_id.upper())[:16] or uuid.uuid4().hex[:16].upper()
        return f"{prefix}_{token}"

    def _create_provisional(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: str,
        proposal: sqlite3.Row,
        session_id: str,
        actor: str,
    ) -> str:
        now = _now()
        evidence = json.loads(str(proposal["evidence_json"] or "{}"))
        evidence = evidence if isinstance(evidence, dict) else {}
        code = self._provisional_code(str(proposal["id"]), str(proposal["kind"]))
        existing = connection.execute(
            """SELECT id,kind,name,status,extra_json FROM story_assets
               WHERE project_id=? AND code=?""",
            (project_id, code),
        ).fetchone()
        if existing is not None:
            try:
                existing_extra = json.loads(str(existing["extra_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                existing_extra = {}
            same_proposal = bool(
                isinstance(existing_extra, dict)
                and str(existing_extra.get("source_asset_proposal_id") or "")
                == str(proposal["id"])
            )
            if (
                same_proposal
                and str(existing["kind"]) == str(proposal["kind"])
                and normalized_entity_name(existing["name"])
                == normalized_entity_name(proposal["name"])
                and str(existing["status"]) == "ACTIVE"
            ):
                return str(existing["id"])
            raise DomainRuleError(
                "PRODUCTION_SESSION_PROVISIONAL_ASSET_CODE_CONFLICT",
                "临时资产编码已被其他资产占用，需要人工核对",
                {
                    "asset_proposal_id": str(proposal["id"]),
                    "existing_asset_id": str(existing["id"]),
                    "code": code,
                },
            )
        asset_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO story_assets
               (id,project_id,kind,code,name,description,canonical_media_version_id,
                extra_json,status,created_at,updated_at,created_by,revision,schema_version)
               VALUES (?,?,?,?,?,?,NULL,?,'ACTIVE',?,?,?,1,'v2')""",
            (
                asset_id,
                project_id,
                proposal["kind"],
                code,
                proposal["name"],
                str(evidence.get("introduction") or evidence.get("description") or ""),
                _json(
                    {
                        "source_asset_proposal_id": str(proposal["id"]),
                        "production_session_id": session_id,
                        "provisional": True,
                        "human_approved": False,
                        "text_dossier": evidence,
                        "media_generation_started": False,
                    }
                ),
                now,
                now,
                actor,
            ),
        )
        connection.execute(
            """UPDATE story_asset_proposals
               SET suggested_asset_id=?,updated_at=?,revision=revision+1
               WHERE id=? AND status='PENDING' AND suggested_asset_id IS NULL""",
            (asset_id, now, proposal["id"]),
        )
        connection.execute(
            """INSERT INTO audit_events
               (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
               VALUES (?,'producer','PRODUCTION_SESSION_PROVISIONAL_ASSET_CREATED',
                       'story_asset',?,'生产会话创建待人工核对的临时资产',?)""",
            (
                actor,
                asset_id,
                _json(
                    {
                        "production_session_id": session_id,
                        "asset_proposal_id": str(proposal["id"]),
                        "human_approved": False,
                    }
                ),
            ),
        )
        return asset_id

    @staticmethod
    def _bind_asset_to_applied_shots(
        connection: sqlite3.Connection,
        *,
        episode_id: str,
        proposal: sqlite3.Row,
        asset_id: str,
        actor: str,
    ) -> int:
        draft = connection.execute(
            "SELECT * FROM script_breakdown_drafts WHERE id=?",
            (proposal["breakdown_draft_id"],),
        ).fetchone()
        if draft is None:
            return 0
        payload, _revision = load_effective_breakdown_draft(connection, draft)
        scenes = payload.get("scenes") if isinstance(payload, dict) else None
        if not isinstance(scenes, list):
            return 0
        episode = connection.execute(
            "SELECT code FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()
        if episode is None:
            return 0
        target_name = normalized_entity_name(proposal["name"])
        kind = str(proposal["kind"])
        role = {"CHARACTER": "main", "SCENE": "location", "PROP": "prop"}.get(kind)
        if role is None:
            return 0
        now = _now()
        bound = 0
        for scene in scenes:
            if not isinstance(scene, dict):
                continue
            scene_no = int(scene.get("scene_no") or 0)
            applied = connection.execute(
                """SELECT 1 FROM script_breakdown_scene_applications
                   WHERE breakdown_draft_id=? AND episode_id=? AND scene_no=?""",
                (proposal["breakdown_draft_id"], episode_id, scene_no),
            ).fetchone()
            if applied is None:
                continue
            scene_characters = [
                str(name).strip()
                for name in (scene.get("characters") or [])
                if str(name).strip()
            ]
            scene_location = normalized_entity_name(scene.get("location"))
            for shot in scene.get("shots") or []:
                if not isinstance(shot, dict):
                    continue
                if kind == "CHARACTER":
                    evidence_names = BreakdownApplyService._shot_character_names(
                        shot, scene_characters
                    )
                elif kind == "SCENE":
                    evidence_names = [scene_location] if scene_location else []
                else:
                    evidence_names = [
                        normalized_entity_name(name)
                        for name in (shot.get("props") or [])
                        if normalized_entity_name(name)
                    ]
                if target_name not in {
                    normalized_entity_name(name) for name in evidence_names
                }:
                    continue
                shot_no = int(shot.get("shot_no") or 0)
                shot_code = f"{episode['code']}-{scene_no:02d}-{shot_no:02d}"
                shot_row = connection.execute(
                    "SELECT id FROM shots WHERE episode_id=? AND code=? AND archived_at IS NULL",
                    (episode_id, shot_code),
                ).fetchone()
                if shot_row is None:
                    continue
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO shot_asset_bindings
                       (id,shot_id,asset_id,role_in_shot,created_at,created_by,revision,schema_version)
                       VALUES (?,?,?,?,?,?,1,'v2')""",
                    (
                        str(uuid.uuid4()),
                        shot_row["id"],
                        asset_id,
                        role,
                        now,
                        actor,
                    ),
                )
                bound += cursor.rowcount
        return bound

    def ensure_episode_inputs(
        self,
        session_id: str,
        episode_id: str,
        *,
        actor: str = "production-session-runner",
    ) -> dict[str, Any]:
        plan = self.plan_episode(session_id, episode_id)
        if not plan["ready"]:
            return {**plan, "registered": [], "bound_shot_count": 0}
        registered: list[dict[str, Any]] = []
        bound_shot_count = 0
        for planned in plan["items"]:
            if planned["status"] in {"FORMAL", "SKIPPED", "SESSION_READY"}:
                continue
            with self.database.transaction() as connection:
                session, session_item = self._session(connection, session_id, episode_id)
                proposal = connection.execute(
                    "SELECT * FROM story_asset_proposals WHERE id=?",
                    (planned["proposal_id"],),
                ).fetchone()
                if proposal is None or str(proposal["status"]) != "PENDING":
                    raise DomainRuleError(
                        "PRODUCTION_SESSION_ASSET_PLAN_STALE",
                        "资产建议在自动准备前已发生变化，请重新规划",
                    )
                existing_input = connection.execute(
                    """SELECT i.id,i.story_asset_id,a.project_id,a.kind,a.status
                       FROM production_session_asset_inputs i
                       JOIN story_assets a ON a.id=i.story_asset_id
                       WHERE i.session_id=? AND i.asset_proposal_id=? AND i.state='ACTIVE'""",
                    (session_id, proposal["id"]),
                ).fetchone()
                if existing_input is not None:
                    if (
                        str(existing_input["project_id"]) != str(session["project_id"])
                        or str(existing_input["kind"]) != str(proposal["kind"])
                        or str(existing_input["status"]) != "ACTIVE"
                    ):
                        raise DomainRuleError(
                            "PRODUCTION_SESSION_ASSET_INPUT_STALE",
                            "会话临时资产已归档、越过项目边界或类型发生变化",
                        )
                    bound_shot_count += self._bind_asset_to_applied_shots(
                        connection,
                        episode_id=episode_id,
                        proposal=proposal,
                        asset_id=str(existing_input["story_asset_id"]),
                        actor=actor,
                    )
                    continue
                asset_id = str(planned.get("asset_id") or "")
                if planned["status"] == "READY_TO_CREATE":
                    asset_id = self._create_provisional(
                        connection,
                        project_id=str(session["project_id"]),
                        proposal=proposal,
                        session_id=session_id,
                        actor=actor,
                    )
                asset = connection.execute(
                    """SELECT id,project_id,kind,name,revision,status FROM story_assets
                       WHERE id=?""",
                    (asset_id,),
                ).fetchone()
                if (
                    asset is None
                    or str(asset["project_id"]) != str(session["project_id"])
                    or str(asset["kind"]) != str(proposal["kind"])
                    or str(asset["status"]) != "ACTIVE"
                ):
                    raise DomainRuleError(
                        "PRODUCTION_SESSION_ASSET_INPUT_INVALID",
                        "临时资产输入不存在、已归档或类型不一致",
                    )
                current_proposal = connection.execute(
                    "SELECT revision FROM story_asset_proposals WHERE id=?",
                    (proposal["id"],),
                ).fetchone()
                content = {
                    "schema_version": "production-session-asset-input.v1",
                    "production_session_id": session_id,
                    "episode_id": episode_id,
                    "asset_proposal_id": str(proposal["id"]),
                    "proposal_revision": int(current_proposal["revision"]),
                    "story_asset_id": asset_id,
                    "asset_revision": int(asset["revision"]),
                    "selection_authority": "MACHINE_TEMPORARY",
                    "human_approved": False,
                }
                input_id = str(uuid.uuid4())
                now = _now()
                connection.execute(
                    """INSERT INTO production_session_asset_inputs
                       (id,session_id,session_item_id,episode_id,asset_proposal_id,
                        story_asset_id,state,content_hash,input_json,created_at,updated_at,
                        created_by,revision,schema_version)
                       VALUES (?,?,?,?,?,?,'ACTIVE',?,?,?,?,?,1,
                               'production-session-asset-input.v1')
                       ON CONFLICT(session_id,asset_proposal_id) DO UPDATE SET
                         story_asset_id=excluded.story_asset_id,state='ACTIVE',
                         content_hash=excluded.content_hash,input_json=excluded.input_json,
                         updated_at=excluded.updated_at,created_by=excluded.created_by,
                         revision=production_session_asset_inputs.revision+1""",
                    (
                        input_id,
                        session_id,
                        session_item["id"],
                        episode_id,
                        proposal["id"],
                        asset_id,
                        _digest(content),
                        _json(content),
                        now,
                        now,
                        actor,
                    ),
                )
                bound_shot_count += self._bind_asset_to_applied_shots(
                    connection,
                    episode_id=episode_id,
                    proposal=proposal,
                    asset_id=asset_id,
                    actor=actor,
                )
                connection.execute(
                    """INSERT INTO audit_events
                       (actor,role_context,action,subject_type,subject_id,summary,
                        metadata_redacted_json)
                       VALUES (?,'producer','PRODUCTION_SESSION_ASSET_INPUT_REGISTERED',
                               'production_session_asset_input',?,
                               '登记机器临时资产身份输入；原建议仍等待人工决定',?)""",
                    (
                        actor,
                        input_id,
                        _json(
                            {
                                "production_session_id": session_id,
                                "asset_proposal_id": str(proposal["id"]),
                                "story_asset_id": asset_id,
                                "human_approved": False,
                            }
                        ),
                    ),
                )
                registered.append(
                    {
                        "id": input_id,
                        "asset_proposal_id": str(proposal["id"]),
                        "story_asset_id": asset_id,
                        "selection_authority": "MACHINE_TEMPORARY",
                        "human_approved": False,
                    }
                )
        refreshed = self.plan_episode(session_id, episode_id)
        return {
            **refreshed,
            "registered": registered,
            "bound_shot_count": bound_shot_count,
            "mutated": bool(registered or bound_shot_count),
        }

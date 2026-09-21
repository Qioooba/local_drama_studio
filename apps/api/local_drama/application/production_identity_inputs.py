"""Session-scoped character identity inputs for unattended production.

These facts deliberately do not approve a Character Identity Pack.  They let
one production session freeze a complete, verified draft pack as a temporary
machine input while the existing approved-pack authority remains unchanged.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from local_drama.application.character_identity_packs import CharacterIdentityPackService
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.config import Settings
from local_drama.domain.character_identity_packs import PackVersionStatus
from local_drama.domain.errors import DomainRuleError


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _session_row(connection: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM production_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if row is None:
        raise DomainRuleError("PRODUCTION_SESSION_NOT_FOUND", "生产会话不存在")
    if str(row["status"]) in {"COMPLETED", "FAILED", "CANCELLED"}:
        raise DomainRuleError(
            "PRODUCTION_SESSION_IDENTITY_INPUT_INACTIVE",
            "已结束的生产会话不能继续使用临时人物身份输入",
            {"production_session_id": session_id, "status": str(row["status"])},
        )
    return cast(sqlite3.Row, row)


def _version_row(connection: sqlite3.Connection, pack_version_id: str) -> sqlite3.Row:
    row = connection.execute(
        """SELECT v.*,p.status AS pack_status,p.code AS pack_code,p.name AS pack_name
           FROM character_identity_pack_versions v
           JOIN character_identity_packs p ON p.id=v.pack_id
           WHERE v.id=?""",
        (pack_version_id,),
    ).fetchone()
    if row is None:
        raise DomainRuleError("IDENTITY_PACK_VERSION_NOT_FOUND", "身份包版本不存在")
    return cast(sqlite3.Row, row)


def _asset_belongs_to_session(
    connection: sqlite3.Connection, session_id: str, story_asset_id: str
) -> bool:
    return connection.execute(
        """SELECT 1 FROM production_session_items psi
           JOIN shots sh ON sh.episode_id=psi.episode_id AND sh.archived_at IS NULL
           JOIN shot_asset_bindings sab ON sab.shot_id=sh.id
           WHERE psi.session_id=? AND sab.asset_id=? LIMIT 1""",
        (session_id, story_asset_id),
    ).fetchone() is not None


def session_identity_snapshot_for_shot(
    connection: sqlite3.Connection,
    production_session_id: str,
    project_id: str,
    shot_id: str,
) -> dict[str, Any] | None:
    """Resolve exact temporary identities for one shot inside one session.

    Every read revalidates the mutable draft pack.  A slot edit therefore
    invalidates the frozen registration instead of silently changing an
    already planned generation.
    """

    session = _session_row(connection, production_session_id)
    if str(session["project_id"]) != project_id:
        raise DomainRuleError(
            "PRODUCTION_SESSION_PROJECT_MISMATCH", "生产会话与镜头不属于同一项目"
        )
    membership = connection.execute(
        """SELECT psi.episode_id FROM shots sh
           JOIN production_session_items psi
             ON psi.episode_id=sh.episode_id AND psi.session_id=?
           WHERE sh.id=? AND sh.archived_at IS NULL""",
        (production_session_id, shot_id),
    ).fetchone()
    if membership is None:
        raise DomainRuleError(
            "PRODUCTION_SESSION_SHOT_OUT_OF_SCOPE", "镜头不属于当前生产会话"
        )
    bindings = connection.execute(
        """SELECT sab.asset_id,sab.asset_state_id,sab.role_in_shot,
                  sab.identity_pack_version_id,a.code,a.name
           FROM shot_asset_bindings sab
           JOIN story_assets a ON a.id=sab.asset_id AND a.kind='CHARACTER'
           WHERE sab.shot_id=?
           ORDER BY sab.role_in_shot,a.code,a.id""",
        (shot_id,),
    ).fetchall()
    if not bindings:
        return None
    try:
        formal_snapshot = CharacterIdentityPackService.generation_snapshot_for_intent(
            connection,
            {"owner_type": "SHOT", "owner_id": shot_id, "project_id": project_id},
        )
    except DomainRuleError:
        # A stale/partial formal binding may still be replaced by a complete
        # session input.  The legacy path continues to surface the error.
        formal_snapshot = None
    formal_by_asset = {
        str(pack["story_asset_id"]): pack for pack in (formal_snapshot or {}).get("packs", [])
    }
    packs: list[dict[str, Any]] = []
    temporary_count = 0
    for binding in bindings:
        formal = formal_by_asset.get(str(binding["asset_id"]))
        if formal is not None:
            packs.append(
                {
                    **formal,
                    "selection_authority": "HUMAN_APPROVED",
                    "human_approved": True,
                }
            )
            continue
        input_row = connection.execute(
            """SELECT * FROM production_session_identity_inputs
               WHERE session_id=? AND story_asset_id=? AND state='ACTIVE'
               ORDER BY created_at DESC,id DESC LIMIT 1""",
            (production_session_id, binding["asset_id"]),
        ).fetchone()
        if input_row is None:
            raise DomainRuleError(
                "PRODUCTION_SESSION_IDENTITY_INPUT_REQUIRED",
                "当前生产会话缺少角色临时三视图输入",
                {"story_asset_id": str(binding["asset_id"]), "shot_id": shot_id},
            )
        version = _version_row(connection, str(input_row["pack_version_id"]))
        if str(version["project_id"]) != project_id or str(version["story_asset_id"]) != str(
            binding["asset_id"]
        ):
            raise DomainRuleError(
                "PRODUCTION_SESSION_IDENTITY_INPUT_MISMATCH",
                "会话临时身份输入与镜头角色不匹配",
            )
        content, content_hash = CharacterIdentityPackService._validated_content(
            connection, version, require_three_view=True
        )
        if str(input_row["content_hash"]) != content_hash:
            raise DomainRuleError(
                "PRODUCTION_SESSION_IDENTITY_INPUT_STALE",
                "临时人物身份包槽位已变化，请重新冻结会话输入",
                {
                    "story_asset_id": str(binding["asset_id"]),
                    "pack_version_id": str(version["id"]),
                },
            )
        packs.append(
            {
                "story_asset_id": str(binding["asset_id"]),
                "asset_state_id": str(binding["asset_state_id"]) if binding["asset_state_id"] else None,
                "pack_asset_state_id": str(version["asset_state_id"]) if version["asset_state_id"] else None,
                "role_in_shot": str(binding["role_in_shot"]),
                "pack_id": str(version["pack_id"]),
                "pack_code": str(version["pack_code"]),
                "pack_name": str(version["pack_name"]),
                "pack_version_id": str(version["id"]),
                "pack_version_no": int(version["version_no"]),
                "pack_version_status": str(version["status"]),
                "content_hash": content_hash,
                "slots": content["slots"],
                "production_identity_input_id": str(input_row["id"]),
                "selection_authority": "MACHINE_TEMPORARY",
                "human_approved": False,
            }
        )
        temporary_count += 1
    snapshot: dict[str, Any] = {
        "schema_version": "localdrama.production-identity-snapshot.v1",
        "production_session_id": production_session_id,
        "shot_id": shot_id,
        "selection_authority": (
            "MACHINE_TEMPORARY" if temporary_count else "HUMAN_APPROVED"
        ),
        "human_approved": temporary_count == 0,
        "packs": packs,
    }
    snapshot["snapshot_hash"] = _digest(snapshot)
    return snapshot


class ProductionIdentityInputService:
    """Register complete draft identity packs for one production session."""

    def __init__(self, database: DatabaseUnitOfWork) -> None:
        self.database = database

    def episode_snapshot(
        self, production_session_id: str, episode_id: str
    ) -> dict[str, Any]:
        """Read-only readiness projection for launch preflight and recovery."""

        with self.database.connect() as connection:
            session = _session_row(connection, production_session_id)
            member = connection.execute(
                "SELECT 1 FROM production_session_items WHERE session_id=? AND episode_id=?",
                (production_session_id, episode_id),
            ).fetchone()
            if member is None:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_EPISODE_OUT_OF_SCOPE", "分集不属于当前生产会话"
                )
            shots = connection.execute(
                """SELECT id,code FROM shots WHERE episode_id=? AND archived_at IS NULL
                   ORDER BY CAST(order_key AS REAL),code,id""",
                (episode_id,),
            ).fetchall()
            snapshots: list[dict[str, Any]] = []
            missing: list[dict[str, Any]] = []
            for shot in shots:
                try:
                    snapshot = session_identity_snapshot_for_shot(
                        connection,
                        production_session_id,
                        str(session["project_id"]),
                        str(shot["id"]),
                    )
                    if snapshot is not None:
                        snapshots.append(snapshot)
                except DomainRuleError as error:
                    missing.append(
                        {
                            "shot_id": str(shot["id"]),
                            "shot_code": str(shot["code"]),
                            "code": error.code,
                            "detail": error.message,
                        }
                    )
        authority = {
            "schema_version": "localdrama.production-episode-identity-snapshot.v1",
            "production_session_id": production_session_id,
            "episode_id": episode_id,
            "snapshots": snapshots,
            "missing": missing,
        }
        all_human = bool(snapshots) and all(
            bool(item.get("human_approved")) for item in snapshots
        )
        return {
            **authority,
            "snapshot_hash": _digest(authority),
            "ready": not missing,
            "human_approved": all_human,
            "selection_authority": (
                "HUMAN_APPROVED" if all_human else "MACHINE_TEMPORARY"
            ),
        }

    def ensure_episode_inputs(
        self,
        production_session_id: str,
        episode_id: str,
        *,
        actor: str = "production-session-worker",
    ) -> dict[str, Any]:
        """Reuse complete drafts for every character actually used by an episode.

        This is intentionally conservative: it never invents a pack, fills a
        missing view, changes a shot binding, or approves a version.
        """

        with self.database.connect() as connection:
            session = _session_row(connection, production_session_id)
            member = connection.execute(
                "SELECT 1 FROM production_session_items WHERE session_id=? AND episode_id=?",
                (production_session_id, episode_id),
            ).fetchone()
            if member is None:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_EPISODE_OUT_OF_SCOPE", "分集不属于当前生产会话"
                )
            bindings = connection.execute(
                """SELECT DISTINCT sab.asset_id,a.code,a.name
                   FROM shots sh JOIN shot_asset_bindings sab ON sab.shot_id=sh.id
                   JOIN story_assets a ON a.id=sab.asset_id AND a.kind='CHARACTER'
                   WHERE sh.episode_id=? AND sh.archived_at IS NULL
                   ORDER BY a.code,a.id""",
                (episode_id,),
            ).fetchall()
            project_id = str(session["project_id"])
        registered: list[dict[str, Any]] = []
        candidate_failures: dict[str, list[str]] = {}
        for binding in bindings:
            story_asset_id = str(binding["asset_id"])
            with self.database.connect() as connection:
                existing = connection.execute(
                    """SELECT pack_version_id FROM production_session_identity_inputs
                       WHERE session_id=? AND story_asset_id=? AND state='ACTIVE'""",
                    (production_session_id, story_asset_id),
                ).fetchone()
                candidates = connection.execute(
                    """SELECT v.id FROM character_identity_pack_versions v
                       JOIN character_identity_packs p ON p.id=v.pack_id
                       WHERE v.project_id=? AND v.story_asset_id=? AND p.status='ACTIVE'
                         AND v.status IN ('DRAFT','READY_FOR_REVIEW')
                       ORDER BY v.version_no DESC,v.created_at DESC,v.id DESC""",
                    (project_id, story_asset_id),
                ).fetchall()
            candidate_ids = [str(existing["pack_version_id"])] if existing is not None else []
            candidate_ids.extend(
                str(row["id"]) for row in candidates if str(row["id"]) not in candidate_ids
            )
            selected: dict[str, Any] | None = None
            failure_codes: list[str] = []
            for candidate_id in candidate_ids:
                try:
                    selected = self.register(
                        production_session_id, candidate_id, actor=actor
                    )
                    break
                except DomainRuleError as error:
                    failure_codes.append(error.code)
            if selected is not None:
                registered.append(selected)
            else:
                candidate_failures[story_asset_id] = list(dict.fromkeys(failure_codes))

        missing: list[dict[str, Any]] = []
        with self.database.connect() as connection:
            shots = connection.execute(
                """SELECT id,code FROM shots WHERE episode_id=? AND archived_at IS NULL
                   ORDER BY CAST(order_key AS REAL),code,id""",
                (episode_id,),
            ).fetchall()
            for shot in shots:
                try:
                    session_identity_snapshot_for_shot(
                        connection,
                        production_session_id,
                        project_id,
                        str(shot["id"]),
                    )
                except DomainRuleError as error:
                    missing.append(
                        {
                            "shot_id": str(shot["id"]),
                            "shot_code": str(shot["code"]),
                            "reason_codes": [error.code],
                            "detail": error.message,
                        }
                    )
        return {
            "production_session_id": production_session_id,
            "episode_id": episode_id,
            "selection_authority": "MACHINE_TEMPORARY",
            "human_approved": False,
            "registered": registered,
            "missing": missing,
            "candidate_failures": candidate_failures,
            "ready": not missing,
        }

    def register(
        self,
        production_session_id: str,
        pack_version_id: str,
        *,
        actor: str = "production-session-worker",
    ) -> dict[str, Any]:
        now = _now()
        with self.database.transaction() as connection:
            session = _session_row(connection, production_session_id)
            version = _version_row(connection, pack_version_id)
            if str(version["pack_status"]) != "ACTIVE":
                raise DomainRuleError("IDENTITY_PACK_NOT_ACTIVE", "人物身份包已归档或废弃")
            if str(version["project_id"]) != str(session["project_id"]):
                raise DomainRuleError(
                    "PRODUCTION_SESSION_PROJECT_MISMATCH", "人物身份包与生产会话不属于同一项目"
                )
            if str(version["status"]) not in {
                PackVersionStatus.DRAFT.value,
                PackVersionStatus.READY_FOR_REVIEW.value,
            }:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_DRAFT_IDENTITY_REQUIRED",
                    "会话临时身份输入只接收尚未人工批准的草稿版本",
                    {"pack_version_status": str(version["status"])},
                )
            story_asset_id = str(version["story_asset_id"])
            if not _asset_belongs_to_session(connection, production_session_id, story_asset_id):
                raise DomainRuleError(
                    "PRODUCTION_SESSION_IDENTITY_ASSET_OUT_OF_SCOPE",
                    "角色资产未用于当前生产会话中的镜头",
                    {"story_asset_id": story_asset_id},
                )
            content, content_hash = CharacterIdentityPackService._validated_content(
                connection, version, require_three_view=True
            )
            existing = connection.execute(
                """SELECT * FROM production_session_identity_inputs
                   WHERE session_id=? AND story_asset_id=? AND state='ACTIVE'""",
                (production_session_id, story_asset_id),
            ).fetchone()
            if existing is not None and str(existing["pack_version_id"]) == pack_version_id and str(
                existing["content_hash"]
            ) == content_hash:
                result = dict(existing)
                result["content"] = json.loads(str(result.pop("content_json")))
                result["idempotent_replay"] = True
                return result
            connection.execute(
                """UPDATE production_session_identity_inputs
                   SET state='SUPERSEDED',updated_at=?,revision=revision+1
                   WHERE session_id=? AND story_asset_id=? AND state='ACTIVE'""",
                (now, production_session_id, story_asset_id),
            )
            input_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO production_session_identity_inputs
                   (id,session_id,story_asset_id,pack_version_id,state,content_hash,content_json,
                    created_at,updated_at,created_by,revision,schema_version)
                   VALUES (?,?,?,?,'ACTIVE',?,?, ?,?,?,1,'production-identity-input.v1')""",
                (
                    input_id,
                    production_session_id,
                    story_asset_id,
                    pack_version_id,
                    content_hash,
                    _json(content),
                    now,
                    now,
                    actor,
                ),
            )
            connection.execute(
                """INSERT INTO audit_events
                   (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                   VALUES (?,'producer','PRODUCTION_IDENTITY_INPUT_REGISTERED',
                           'production_session_identity_input',?, '冻结会话临时人物身份输入',?)""",
                (
                    actor,
                    input_id,
                    _json(
                        {
                            "production_session_id": production_session_id,
                            "story_asset_id": story_asset_id,
                            "pack_version_id": pack_version_id,
                            "pack_version_status": str(version["status"]),
                            "human_approved": False,
                            "content_hash": content_hash,
                        }
                    ),
                ),
            )
        return {
            "id": input_id,
            "session_id": production_session_id,
            "story_asset_id": story_asset_id,
            "pack_version_id": pack_version_id,
            "state": "ACTIVE",
            "content_hash": content_hash,
            "content": content,
            "idempotent_replay": False,
        }


class ProductionIdentityHeroPreparationService:
    """Prepare missing bound-asset HERO references with existing T2I batches."""

    ACTIVE_JOB_STATES = frozenset({"QUEUED", "CLAIMED", "RUNNING", "SUCCEEDED"})

    def __init__(self, database: DatabaseUnitOfWork, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def plan_episode(self, production_session_id: str, episode_id: str) -> dict[str, Any]:
        from local_drama.infrastructure.service_composition import build_asset_image_batch

        with self.database.connect() as connection:
            session = _session_row(connection, production_session_id)
            member = connection.execute(
                "SELECT id FROM production_session_items WHERE session_id=? AND episode_id=?",
                (production_session_id, episode_id),
            ).fetchone()
            if member is None:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_EPISODE_OUT_OF_SCOPE", "分集不属于当前生产会话"
                )
            assets = [
                dict(row)
                for row in connection.execute(
                    """SELECT DISTINCT a.id,a.code,a.name,a.kind,
                              EXISTS(SELECT 1 FROM story_asset_references r
                                     JOIN media_versions mv ON mv.id=r.media_version_id
                                     WHERE r.story_asset_id=a.id AND r.reference_kind='HERO'
                                       AND r.status='ACTIVE'
                                       AND mv.integrity_status='VERIFIED') AS has_hero
                       FROM shots sh JOIN shot_asset_bindings sab ON sab.shot_id=sh.id
                       JOIN story_assets a ON a.id=sab.asset_id
                       WHERE sh.episode_id=? AND sh.archived_at IS NULL
                       ORDER BY a.kind,a.code,a.id""",
                    (episode_id,),
                ).fetchall()
            ]
            linked_rows = connection.execute(
                """SELECT gi.owner_id AS asset_id,j.id AS job_id,j.state
                   FROM production_session_job_links psl
                   JOIN jobs j ON j.id=psl.job_id
                   JOIN generation_variants gv ON gv.id=j.subject_id
                   JOIN generation_intents gi ON gi.id=gv.intent_id
                   WHERE psl.session_id=? AND psl.session_item_id=?
                     AND psl.link_state='ACTIVE' AND psl.role='IDENTITY_HERO'
                     AND gi.owner_type='STORY_ASSET' AND gi.purpose='ASSET_HERO_IMAGE'""",
                (production_session_id, str(member["id"])),
            ).fetchall()
        linked_by_asset: dict[str, list[dict[str, Any]]] = {}
        for row in linked_rows:
            linked_by_asset.setdefault(str(row["asset_id"]), []).append(dict(row))

        unresolved_by_kind: dict[str, list[str]] = {}
        for asset in assets:
            asset_id = str(asset["id"])
            if not bool(asset["has_hero"]) and not linked_by_asset.get(asset_id):
                unresolved_by_kind.setdefault(str(asset["kind"]), []).append(asset_id)
        batch_plans: list[dict[str, Any]] = []
        batch_items: dict[str, dict[str, Any]] = {}
        batch_service = build_asset_image_batch(self.database, self.settings)
        for kind, unresolved in sorted(unresolved_by_kind.items()):
            for offset in range(0, len(unresolved), 100):
                asset_ids = unresolved[offset : offset + 100]
                planned = batch_service.plan(
                    str(session["project_id"]),
                    asset_kind=kind,
                    asset_ids=asset_ids,
                )
                batch_plan = {
                    **planned,
                    "asset_kind": kind,
                    "asset_ids": asset_ids,
                    "chunk_index": offset // 100,
                }
                batch_plans.append(batch_plan)
                batch_items.update(
                    {
                        str(item["asset_id"]): {
                            **dict(item),
                            "asset_kind": kind,
                            "batch_plan_hash": str(planned["plan_hash"]),
                            "chunk_index": offset // 100,
                        }
                        for item in planned["items"]
                    }
                )

        items: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        for asset in assets:
            asset_id = str(asset["id"])
            links = linked_by_asset.get(asset_id, [])
            item: dict[str, Any]
            if bool(asset["has_hero"]):
                item = {
                    "asset_id": asset_id,
                    "asset_code": str(asset["code"]),
                    "asset_kind": str(asset["kind"]),
                    "status": "READY",
                    "job_ids": [],
                }
            elif any(str(link["state"]) in self.ACTIVE_JOB_STATES for link in links):
                item = {
                    "asset_id": asset_id,
                    "asset_code": str(asset["code"]),
                    "asset_kind": str(asset["kind"]),
                    "status": "ACTIVE",
                    "job_ids": [
                        str(link["job_id"])
                        for link in links
                        if str(link["state"]) in self.ACTIVE_JOB_STATES
                    ],
                }
            elif links:
                item = {
                    "asset_id": asset_id,
                    "asset_code": str(asset["code"]),
                    "asset_kind": str(asset["kind"]),
                    "status": "BLOCKED",
                    "job_ids": [],
                    "blockers": [
                        {
                            "code": "PRODUCTION_SESSION_HERO_JOB_FAILED",
                            "message": "当前会话的资产主图生成任务已终止，需要检查失败原因后重试",
                            "job_ids": [str(link["job_id"]) for link in links],
                            "job_states": [str(link["state"]) for link in links],
                        }
                    ],
                }
            else:
                planned = batch_items.get(asset_id) or {}
                planned_status = str(planned.get("status") or "")
                planned_blockers = list(planned.get("blockers") or [])
                if not planned_status and not planned_blockers:
                    planned_blockers = [
                        {
                            "code": "PRODUCTION_SESSION_HERO_PLAN_MISSING",
                            "message": "关键资产主图计划缺少对应项，需重新检查资产与配置",
                        }
                    ]
                item = {
                    "asset_id": asset_id,
                    "asset_code": str(asset["code"]),
                    "asset_kind": str(asset["kind"]),
                    "asset_name": str(asset["name"]),
                    "status": (
                        "READY_TO_SUBMIT"
                        if planned_status == "READY"
                        else "READY"
                        if planned_status == "SKIPPED"
                        else "BLOCKED"
                    ),
                    "job_ids": [],
                    "blockers": planned_blockers,
                }
            items.append(item)
            blockers.extend(
                {**blocker, "asset_id": asset_id, "asset_code": str(asset["code"])}
                for blocker in item.get("blockers", [])
            )
        return {
            "production_session_id": production_session_id,
            "episode_id": episode_id,
            "project_id": str(session["project_id"]),
            "session_item_id": str(member["id"]),
            "items": items,
            "blockers": blockers,
            "ready": not blockers,
            "batch_plan": batch_plans[0] if len(batch_plans) == 1 else None,
            "batch_plans": batch_plans,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }

    def dispatch_episode(
        self,
        production_session_id: str,
        episode_id: str,
        *,
        actor: str = "production-session-worker",
    ) -> dict[str, Any]:
        from local_drama.infrastructure.service_composition import build_asset_image_batch

        self.reconcile_completed(production_session_id, episode_id)
        plan = self.plan_episode(production_session_id, episode_id)
        if not plan["ready"]:
            return {**plan, "submitted": [], "dependency_job_ids": []}
        dependency_job_ids = [
            str(job_id)
            for item in plan["items"]
            for job_id in item.get("job_ids", [])
        ]
        submitted: list[dict[str, Any]] = []
        for batch_plan in plan["batch_plans"]:
            ready_ids = [
                str(item["asset_id"])
                for item in plan["items"]
                if item["status"] == "READY_TO_SUBMIT"
                and str(item["asset_kind"]) == str(batch_plan["asset_kind"])
                and str(item["asset_id"]) in set(batch_plan["asset_ids"])
            ]
            if not ready_ids:
                continue
            result = build_asset_image_batch(self.database, self.settings).submit(
                str(plan["project_id"]),
                asset_kind=str(batch_plan["asset_kind"]),
                asset_ids=ready_ids,
                expected_plan_hash=str(batch_plan["plan_hash"]),
                idempotency_key=(
                    f"production-session:{production_session_id}:episode:{episode_id}:"
                    f"hero:{str(batch_plan['asset_kind']).casefold()}:"
                    f"{int(batch_plan['chunk_index'])}:v2"
                ),
                actor=actor,
            )
            for item in result["items"]:
                job_id = str(item.get("job_id") or "")
                if not job_id:
                    continue
                dependency_job_ids.append(job_id)
                with self.database.transaction() as connection:
                    connection.execute(
                        """INSERT OR IGNORE INTO production_session_job_links
                           (id,session_id,session_item_id,job_id,stage_code,role,link_state,
                            created_at,updated_at,created_by,revision,schema_version)
                           VALUES (?,?,?,?,?,'IDENTITY_HERO','ACTIVE',?,?,?,1,'production-session.v1')""",
                        (
                            str(uuid.uuid4()),
                            production_session_id,
                            str(plan["session_item_id"]),
                            job_id,
                            "ASSETS",
                            _now(),
                            _now(),
                            actor,
                        ),
                    )
                submitted.append(
                    {"asset_id": str(item["asset_id"]), "job_id": job_id}
                )
        return {
            **plan,
            "submitted": submitted,
            "dependency_job_ids": list(dict.fromkeys(dependency_job_ids)),
            "mutated": bool(submitted),
        }

    def reconcile_completed(self, production_session_id: str, episode_id: str) -> None:
        """Finish media adoption after a worker crash or cross-worker hand-off."""

        from local_drama.infrastructure.service_composition import build_asset_image_completion

        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT j.id FROM production_session_job_links psl
                   JOIN production_session_items psi ON psi.id=psl.session_item_id
                   JOIN jobs j ON j.id=psl.job_id
                   WHERE psl.session_id=? AND psi.episode_id=?
                     AND psl.link_state='ACTIVE' AND psl.role='IDENTITY_HERO'
                     AND j.state='SUCCEEDED'""",
                (production_session_id, episode_id),
            ).fetchall()
        completion = build_asset_image_completion(self.database, self.settings)
        for row in rows:
            job_id = str(row["id"])
            with self.database.connect() as connection:
                artifacts = [
                    dict(item)
                    for item in connection.execute(
                        """SELECT a.* FROM artifacts a
                           JOIN job_attempts ja ON ja.id=a.job_attempt_id
                           WHERE ja.job_id=? AND ja.state='SUCCEEDED' AND a.status='VERIFIED'
                           ORDER BY a.created_at,a.id""",
                        (job_id,),
                    ).fetchall()
                ]
            try:
                completion.finalize_job(job_id, artifacts)
            except DomainRuleError as error:
                completion.record_finalization_failure(job_id, error)


class ProductionIdentityPreparationService:
    """Dispatch existing multi-view generation for session identity gaps."""

    REQUIRED_SLOTS = ("FRONT", "LEFT", "RIGHT")

    def __init__(self, database: DatabaseUnitOfWork, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.inputs = ProductionIdentityInputService(database)

    def plan_episode(self, production_session_id: str, episode_id: str) -> dict[str, Any]:
        from local_drama.application.asset_multiview import AssetMultiViewService

        with self.database.connect() as connection:
            session = _session_row(connection, production_session_id)
            member = connection.execute(
                "SELECT id FROM production_session_items WHERE session_id=? AND episode_id=?",
                (production_session_id, episode_id),
            ).fetchone()
            if member is None:
                raise DomainRuleError(
                    "PRODUCTION_SESSION_EPISODE_OUT_OF_SCOPE", "分集不属于当前生产会话"
                )
            assets = connection.execute(
                """SELECT DISTINCT a.id,a.code,a.name
                   FROM shots sh JOIN shot_asset_bindings sab ON sab.shot_id=sh.id
                   JOIN story_assets a ON a.id=sab.asset_id AND a.kind='CHARACTER'
                   WHERE sh.episode_id=? AND sh.archived_at IS NULL
                   ORDER BY a.code,a.id""",
                (episode_id,),
            ).fetchall()
            active_inputs = {
                str(row["story_asset_id"])
                for row in connection.execute(
                    """SELECT story_asset_id FROM production_session_identity_inputs
                       WHERE session_id=? AND state='ACTIVE'""",
                    (production_session_id,),
                ).fetchall()
            }
            occurrence_counts = {
                str(row["asset_id"]): int(row["shot_count"])
                for row in connection.execute(
                    """SELECT sab.asset_id,COUNT(DISTINCT sh.id) AS shot_count
                       FROM shots sh JOIN shot_asset_bindings sab ON sab.shot_id=sh.id
                       JOIN story_assets a ON a.id=sab.asset_id AND a.kind='CHARACTER'
                       WHERE sh.episode_id=? AND sh.archived_at IS NULL
                       GROUP BY sab.asset_id""",
                    (episode_id,),
                ).fetchall()
            }
            formal_occurrence_counts: dict[str, int] = {}
            shot_rows = connection.execute(
                "SELECT id FROM shots WHERE episode_id=? AND archived_at IS NULL",
                (episode_id,),
            ).fetchall()
            for shot in shot_rows:
                try:
                    snapshot = CharacterIdentityPackService.generation_snapshot_for_intent(
                        connection,
                        {
                            "owner_type": "SHOT",
                            "owner_id": str(shot["id"]),
                            "project_id": str(session["project_id"]),
                        },
                    )
                except DomainRuleError:
                    continue
                for asset_id in {
                    str(pack["story_asset_id"])
                    for pack in (snapshot or {}).get("packs", [])
                }:
                    formal_occurrence_counts[asset_id] = (
                        formal_occurrence_counts.get(asset_id, 0) + 1
                    )
            formal_assets = {
                asset_id
                for asset_id, count in formal_occurrence_counts.items()
                if count == occurrence_counts.get(asset_id)
            }
            linked = connection.execute(
                """SELECT gi.owner_id AS asset_id,psl.role,j.id AS job_id,j.state
                   FROM production_session_job_links psl
                   JOIN jobs j ON j.id=psl.job_id
                   JOIN generation_variants gv ON gv.id=j.subject_id
                   JOIN generation_intents gi ON gi.id=gv.intent_id
                   WHERE psl.session_id=? AND psl.session_item_id=?
                     AND psl.link_state='ACTIVE' AND psl.role LIKE 'IDENTITY_VIEW_%'
                     AND gi.owner_type='STORY_ASSET' AND gi.purpose='ASSET_MULTI_VIEW'""",
                (production_session_id, str(member["id"])),
            ).fetchall()
        linked_by_asset: dict[str, list[dict[str, Any]]] = {}
        for row in linked:
            linked_by_asset.setdefault(str(row["asset_id"]), []).append(dict(row))
        items: list[dict[str, Any]] = []
        service = AssetMultiViewService(self.database, self.settings)
        for asset in assets:
            asset_id = str(asset["id"])
            if asset_id in active_inputs or asset_id in formal_assets:
                items.append(
                    {
                        "asset_id": asset_id,
                        "asset_code": str(asset["code"]),
                        "status": "READY",
                        "selection_authority": (
                            "MACHINE_TEMPORARY"
                            if asset_id in active_inputs
                            else "HUMAN_APPROVED"
                        ),
                    }
                )
                continue
            links = linked_by_asset.get(asset_id, [])
            active_links = [
                row
                for row in links
                if str(row["state"]) in {"QUEUED", "CLAIMED", "RUNNING", "SUCCEEDED"}
            ]
            if active_links:
                items.append(
                    {
                        "asset_id": asset_id,
                        "asset_code": str(asset["code"]),
                        "status": "ACTIVE",
                        "job_ids": [str(row["job_id"]) for row in active_links],
                    }
                )
                continue
            preflight = service.preflight(asset_id, requested_slots=list(self.REQUIRED_SLOTS))
            items.append(
                {
                    "asset_id": asset_id,
                    "asset_code": str(asset["code"]),
                    "asset_name": str(asset["name"]),
                    "status": "READY_TO_SUBMIT" if preflight["ready"] else "BLOCKED",
                    "preflight": preflight,
                    "blockers": preflight["blockers"],
                }
            )
        blockers = [
            {**blocker, "asset_id": item["asset_id"], "asset_code": item["asset_code"]}
            for item in items
            for blocker in item.get("blockers", [])
        ]
        return {
            "production_session_id": production_session_id,
            "episode_id": episode_id,
            "project_id": str(session["project_id"]),
            "items": items,
            "blockers": blockers,
            "ready": not blockers,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }

    def dispatch_episode(
        self,
        production_session_id: str,
        episode_id: str,
        *,
        actor: str = "production-session-worker",
    ) -> dict[str, Any]:
        from local_drama.application.asset_multiview import AssetMultiViewService

        self._reconcile_completed(production_session_id, episode_id)
        self.inputs.ensure_episode_inputs(
            production_session_id, episode_id, actor=actor
        )
        plan = self.plan_episode(production_session_id, episode_id)
        if not plan["ready"]:
            return {**plan, "submitted": [], "dependency_job_ids": []}
        with self.database.connect() as connection:
            item_row = connection.execute(
                "SELECT id FROM production_session_items WHERE session_id=? AND episode_id=?",
                (production_session_id, episode_id),
            ).fetchone()
        assert item_row is not None
        submitted: list[dict[str, Any]] = []
        dependency_job_ids: list[str] = []
        service = AssetMultiViewService(self.database, self.settings)
        for item in plan["items"]:
            dependency_job_ids.extend(str(value) for value in item.get("job_ids", []))
            if item["status"] != "READY_TO_SUBMIT":
                continue
            preflight = item["preflight"]
            result = service.submit(
                str(item["asset_id"]),
                plan_hash=str(preflight["plan_hash"]),
                idempotency_key=(
                    f"production-session:{production_session_id}:asset:{item['asset_id']}:multiview:v1"
                ),
                requested_slots=list(self.REQUIRED_SLOTS),
            )
            for generated in result["items"]:
                slot = str(generated["reference_kind"])
                job_id = str(generated["job"]["id"])
                dependency_job_ids.append(job_id)
                with self.database.transaction() as connection:
                    connection.execute(
                        """INSERT OR IGNORE INTO production_session_job_links
                           (id,session_id,session_item_id,job_id,stage_code,role,link_state,
                            created_at,updated_at,created_by,revision,schema_version)
                           VALUES (?,?,?,?,? ,?,'ACTIVE',?,?,?,1,'production-session.v1')""",
                        (
                            str(uuid.uuid4()),
                            production_session_id,
                            str(item_row["id"]),
                            job_id,
                            "ASSETS",
                            f"IDENTITY_VIEW_{slot}",
                            _now(),
                            _now(),
                            actor,
                        ),
                    )
                submitted.append(
                    {"asset_id": str(item["asset_id"]), "slot_kind": slot, "job_id": job_id}
                )
        return {
            **plan,
            "submitted": submitted,
            "dependency_job_ids": list(dict.fromkeys(dependency_job_ids)),
            "mutated": bool(submitted),
        }

    def _reconcile_completed(self, production_session_id: str, episode_id: str) -> None:
        completion = ProductionIdentityGenerationCompletionService(
            self.database, self.settings
        )
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT j.id FROM production_session_job_links psl
                   JOIN production_session_items psi ON psi.id=psl.session_item_id
                   JOIN jobs j ON j.id=psl.job_id
                   WHERE psl.session_id=? AND psi.episode_id=?
                     AND psl.link_state='ACTIVE' AND psl.role LIKE 'IDENTITY_VIEW_%'
                     AND j.state='SUCCEEDED'""",
                (production_session_id, episode_id),
            ).fetchall()
        for row in rows:
            job_id = str(row["id"])
            with self.database.connect() as connection:
                artifacts = [
                    dict(item)
                    for item in connection.execute(
                        """SELECT a.* FROM artifacts a
                           JOIN job_attempts ja ON ja.id=a.job_attempt_id
                           WHERE ja.job_id=? AND ja.state='SUCCEEDED' AND a.status='VERIFIED'
                           ORDER BY a.created_at,a.id""",
                        (job_id,),
                    ).fetchall()
                ]
            try:
                completion.finalize_job(job_id, artifacts)
            except DomainRuleError as error:
                completion.record_failure(job_id, error)


class ProductionIdentityGenerationCompletionService:
    """Adopt verified session multi-view outputs and assemble a draft pack."""

    def __init__(self, database: DatabaseUnitOfWork, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def finalize_job(
        self, job_id: str, artifacts: tuple[dict[str, Any], ...] | list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        from local_drama.application.commands.asset_bible import AssetBibleCommandService
        from local_drama.application.media import MediaService
        from local_drama.application.workspace_assets import WorkspaceAssetService
        from local_drama.infrastructure.database.asset_bible_repository import (
            SqliteAssetBibleRepository,
        )

        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT psl.session_id,psl.session_item_id,psl.role,ps.project_id,
                          psi.episode_id,gi.owner_id AS asset_id,gi.purpose
                   FROM production_session_job_links psl
                   JOIN production_sessions ps ON ps.id=psl.session_id
                   JOIN production_session_items psi ON psi.id=psl.session_item_id
                   JOIN jobs j ON j.id=psl.job_id
                   JOIN generation_variants gv ON gv.id=j.subject_id
                   JOIN generation_intents gi ON gi.id=gv.intent_id
                   WHERE psl.job_id=? AND psl.link_state='ACTIVE'
                     AND psl.role LIKE 'IDENTITY_VIEW_%'""",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        if str(row["purpose"]) != "ASSET_MULTI_VIEW":
            raise DomainRuleError(
                "PRODUCTION_IDENTITY_JOB_SCOPE_INVALID", "会话身份视角 Job 来源不正确"
            )
        slot_kind = str(row["role"]).removeprefix("IDENTITY_VIEW_")
        if slot_kind not in {"FRONT", "LEFT", "RIGHT"}:
            raise DomainRuleError(
                "PRODUCTION_IDENTITY_SLOT_INVALID", "会话身份视角槽位不受支持"
            )
        with self.database.connect() as connection:
            existing = connection.execute(
                """SELECT id,media_version_id FROM story_asset_references
                   WHERE story_asset_id=? AND reference_kind=? AND status='ACTIVE'
                   ORDER BY is_locked DESC,priority,created_at,id LIMIT 1""",
                (row["asset_id"], slot_kind),
            ).fetchone()
        media_version_id: str
        reference_id: str
        if existing is not None:
            media_version_id = str(existing["media_version_id"])
            reference_id = str(existing["id"])
        else:
            if not artifacts:
                raise DomainRuleError(
                    "PRODUCTION_IDENTITY_OUTPUT_MISSING", "三视图任务成功但没有可登记的图片产物"
                )
            media = MediaService(self.database, self.settings)
            promoted: dict[str, Any] | None = None
            last_error: DomainRuleError | None = None
            for artifact in artifacts:
                try:
                    promoted = media.promote_job_artifact(
                        str(artifact["id"]),
                        purpose="PRODUCTION_IDENTITY_VIEW_GENERATED",
                        media_kind="IMAGE",
                        stage="KEYFRAME",
                        actor="production-session-worker",
                    )
                    break
                except DomainRuleError as error:
                    last_error = error
            if promoted is None:
                raise last_error or DomainRuleError(
                    "PRODUCTION_IDENTITY_OUTPUT_INVALID", "三视图任务没有有效图片产物"
                )
            media_version_id = str(promoted["media_version_id"])
            media.submit_default_derivatives(media_version_id)
            WorkspaceAssetService(self.database, self.settings).authorize_media_version(
                str(row["project_id"]), media_version_id, actor="production-session-worker"
            )
            with self.database.transaction() as connection:
                command = AssetBibleCommandService(SqliteAssetBibleRepository(connection))
                reference = command.add_reference(
                    str(row["project_id"]),
                    str(row["asset_id"]),
                    media_version_id,
                    slot_kind,
                    label=f"生产会话自动三视图 · {slot_kind}",
                    priority=20,
                    is_locked=False,
                    actor="production-session-worker",
                )
                reference_id = str(reference["id"])
        pack_version_id = self._assemble_and_register(
            str(row["session_id"]), str(row["project_id"]), str(row["asset_id"])
        )
        return {
            "status": "SUCCEEDED",
            "production_session_id": str(row["session_id"]),
            "asset_id": str(row["asset_id"]),
            "slot_kind": slot_kind,
            "media_version_id": media_version_id,
            "reference_id": reference_id,
            "pack_version_id": pack_version_id,
            "human_approved": False,
        }

    def record_failure(self, job_id: str, error: DomainRuleError) -> bool:
        now = _now()
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT psl.session_item_id FROM production_session_job_links psl
                   WHERE psl.job_id=? AND psl.link_state='ACTIVE'
                     AND psl.role LIKE 'IDENTITY_VIEW_%'""",
                (job_id,),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                """UPDATE production_session_items
                   SET state='BLOCKED',current_stage='ASSETS',last_error_code=?,
                       last_error_message=?,updated_at=?,revision=revision+1 WHERE id=?""",
                (error.code, error.message, now, str(row["session_item_id"])),
            )
            connection.execute(
                """INSERT INTO audit_events
                   (actor,role_context,action,subject_type,subject_id,job_id,summary,metadata_redacted_json)
                   VALUES ('production-session-worker','producer',
                           'PRODUCTION_IDENTITY_OUTPUT_FINALIZATION_FAILED',
                           'production_session_item',?,?,?,?)""",
                (
                    str(row["session_item_id"]),
                    job_id,
                    "人物三视图生成成功，但会话身份登记失败",
                    _json({"error_code": error.code}),
                ),
            )
            return True

    def _assemble_and_register(
        self, production_session_id: str, project_id: str, asset_id: str
    ) -> str | None:
        with self.database.connect() as connection:
            references = connection.execute(
                """SELECT reference_kind,media_version_id FROM story_asset_references
                   WHERE story_asset_id=? AND reference_kind IN ('FRONT','LEFT','RIGHT')
                     AND status='ACTIVE'
                   ORDER BY reference_kind,is_locked DESC,priority,created_at,id""",
                (asset_id,),
            ).fetchall()
            by_kind: dict[str, str] = {}
            for reference in references:
                by_kind.setdefault(
                    str(reference["reference_kind"]), str(reference["media_version_id"])
                )
            if set(by_kind) != {"FRONT", "LEFT", "RIGHT"}:
                return None
            draft = connection.execute(
                """SELECT v.id FROM character_identity_pack_versions v
                   JOIN character_identity_packs p ON p.id=v.pack_id
                   WHERE v.story_asset_id=? AND p.status='ACTIVE'
                     AND v.status IN ('DRAFT','READY_FOR_REVIEW')
                   ORDER BY v.version_no DESC,v.created_at DESC,v.id DESC LIMIT 1""",
                (asset_id,),
            ).fetchone()
            asset = connection.execute(
                "SELECT code,name FROM story_assets WHERE id=?", (asset_id,)
            ).fetchone()
        packs = CharacterIdentityPackService(self.database)
        if draft is None:
            assert asset is not None
            pack = packs.create_pack(
                project_id,
                asset_id,
                f"SESSION_AUTO_{asset_id.replace('-', '')[:12]}",
                f"{asset['name']} · 会话自动草稿",
                actor="production-session-worker",
            )
            pack_version_id = str(pack["versions"][0]["id"])
        else:
            pack_version_id = str(draft["id"])
        for slot_kind in ("FRONT", "LEFT", "RIGHT"):
            packs.set_version_slot(
                pack_version_id,
                slot_kind,
                by_kind[slot_kind],
                actor="production-session-worker",
            )
        ProductionIdentityInputService(self.database).register(
            production_session_id,
            pack_version_id,
            actor="production-session-worker",
        )
        return pack_version_id

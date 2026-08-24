from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class BeatReplanService:
    """Reviewable AI replan over one existing shot group only."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def plan(
        self, *, episode_id: str, group_id: str, draft_id: str,
        proposal_scene_no: int, expected_group_revision: int,
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            return self._build_plan(
                connection, episode_id=episode_id, group_id=group_id, draft_id=draft_id,
                proposal_scene_no=proposal_scene_no, expected_group_revision=expected_group_revision,
            )

    def apply(
        self, *, episode_id: str, group_id: str, draft_id: str, proposal_scene_no: int,
        expected_group_revision: int, expected_plan_hash: str, idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        scope = f"beat-replan:{episode_id}:{group_id}"
        payload_hash = hashlib.sha256(_json({
            "draft_id": draft_id, "proposal_scene_no": proposal_scene_no,
            "expected_group_revision": expected_group_revision, "expected_plan_hash": expected_plan_hash,
        }).encode()).hexdigest()
        with self.database.transaction() as connection:
            previous = connection.execute(
                "SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
                (scope, idempotency_key),
            ).fetchone()
            if previous:
                if previous["payload_hash"] != payload_hash:
                    raise DomainRuleError("IDEMPOTENCY_PAYLOAD_MISMATCH", "幂等键已用于不同的 Beat Replan 请求")
                return json.loads(str(previous["response_json"]))

            plan = self._build_plan(
                connection, episode_id=episode_id, group_id=group_id, draft_id=draft_id,
                proposal_scene_no=proposal_scene_no, expected_group_revision=expected_group_revision,
            )
            if plan["plan_hash"] != expected_plan_hash:
                raise DomainRuleError("BEAT_REPLAN_PLAN_STALE", "Beat Replan 计划已变化，请重新预览")
            if not plan["valid"]:
                raise DomainRuleError("BEAT_REPLAN_PLAN_INVALID", "Beat Replan 计划包含冲突", {"issues": plan["issues"]})

            now = _now()
            created_ids: list[str] = []
            modified_ids: list[str] = []
            archived_ids: list[str] = []
            protected_ids: list[str] = []
            final_ids: list[str] = []
            for item in plan["diff"]:
                action = str(item["action"])
                if action in {"KEEP", "PROTECTED"}:
                    if item.get("shot_id"):
                        final_ids.append(str(item["shot_id"]))
                    if action == "PROTECTED":
                        protected_ids.append(str(item["shot_id"]))
                    continue
                if action == "MODIFY":
                    shot_id = str(item["shot_id"])
                    shot = connection.execute(
                        "SELECT revision,current_revision_id FROM shots WHERE id=? AND episode_id=? AND archived_at IS NULL",
                        (shot_id, episode_id),
                    ).fetchone()
                    if shot is None or int(shot["revision"]) != int(item["expected_revision"]):
                        raise DomainRuleError("SHOT_REVISION_CONFLICT", "镜头已变化，请重新预览", {"shot_id": shot_id})
                    revision_id = str(uuid.uuid4())
                    revision_no = int(connection.execute(
                        "SELECT COALESCE(MAX(revision_no),0)+1 FROM shot_revisions WHERE shot_id=?", (shot_id,),
                    ).fetchone()[0])
                    connection.execute(
                        """INSERT INTO shot_revisions
                        (id,shot_id,revision_no,fields_json,is_frozen,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,0,?,?,?,1,'v2')""",
                        (revision_id, shot_id, revision_no, _json(item["after"]["fields"]), now, now, actor),
                    )
                    connection.execute(
                        """UPDATE shots SET current_revision_id=?,target_duration_ms=?,shot_type=?,status='DIRECTED',
                        revision=revision+1,updated_at=? WHERE id=?""",
                        (revision_id, item["after"]["target_duration_ms"], item["after"]["shot_type"], now, shot_id),
                    )
                    self._mark_variants_stale(connection, shot_id, "beat_replan_changed")
                    modified_ids.append(shot_id)
                    final_ids.append(shot_id)
                    continue
                if action == "ADD":
                    shot_id, revision_id = str(uuid.uuid4()), str(uuid.uuid4())
                    connection.execute(
                        """INSERT INTO shots
                        (id,episode_id,code,order_key,target_duration_ms,shot_type,status,current_revision_id,scene_id,
                        created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,'0',?,?,'DRAFT',?,?, ?,?,?,1,'v2')""",
                        (shot_id, episode_id, item["after"]["code"], item["after"]["target_duration_ms"],
                         item["after"]["shot_type"], revision_id, plan["scene_id"], now, now, actor),
                    )
                    connection.execute(
                        """INSERT INTO shot_revisions
                        (id,shot_id,revision_no,fields_json,is_frozen,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,1,?,0,?,?,?,1,'v2')""",
                        (revision_id, shot_id, _json(item["after"]["fields"]), now, now, actor),
                    )
                    created_ids.append(shot_id)
                    final_ids.append(shot_id)
                    continue
                if action == "DELETE":
                    shot_id = str(item["shot_id"])
                    changed = connection.execute(
                        """UPDATE shots SET archived_at=?,updated_at=?
                        WHERE id=? AND episode_id=? AND revision=? AND archived_at IS NULL""",
                        (now, now, shot_id, episode_id, item["expected_revision"]),
                    )
                    if changed.rowcount != 1:
                        raise DomainRuleError("SHOT_REVISION_CONFLICT", "待归档镜头已变化，请重新预览", {"shot_id": shot_id})
                    self._mark_variants_stale(connection, shot_id, "beat_replan_archived")
                    archived_ids.append(shot_id)

            connection.execute("DELETE FROM shot_group_members WHERE group_id=?", (group_id,))
            connection.executemany(
                "INSERT INTO shot_group_members (group_id,shot_id,order_key,created_at,created_by) VALUES (?,?,?,?,?)",
                [(group_id, shot_id, f"{index:04d}", now, actor) for index, shot_id in enumerate(final_ids, 1)],
            )
            changed_group = connection.execute(
                """UPDATE shot_groups SET revision=revision+1,updated_at=?
                WHERE id=? AND episode_id=? AND revision=? AND status='ACTIVE'""",
                (now, group_id, episode_id, expected_group_revision),
            )
            if changed_group.rowcount != 1:
                raise DomainRuleError("SHOT_GROUP_REVISION_CONFLICT", "镜头组已变化，请重新预览")
            for ordinal, row in enumerate(connection.execute(
                "SELECT id FROM shots WHERE episode_id=? AND archived_at IS NULL ORDER BY CAST(order_key AS REAL),code,id",
                (episode_id,),
            ).fetchall(), 1):
                connection.execute("UPDATE shots SET order_key=? WHERE id=?", (f"{ordinal * 10:08d}", row["id"]))
            stale_timelines = connection.execute(
                "UPDATE timeline_revisions SET status='STALE',updated_at=? WHERE episode_id=? AND status != 'STALE'",
                (now, episode_id),
            ).rowcount

            project_id = connection.execute(
                "SELECT se.project_id FROM episodes e JOIN seasons se ON se.id=e.season_id WHERE e.id=?", (episode_id,),
            ).fetchone()["project_id"]
            response = {
                "plan_hash": expected_plan_hash, "group_id": group_id,
                "group_revision": expected_group_revision + 1, "created_shot_ids": created_ids,
                "modified_shot_ids": modified_ids, "archived_shot_ids": archived_ids,
                "protected_shot_ids": protected_ids, "ordered_member_shot_ids": final_ids,
                "historical_variants_deleted": 0, "stale_timeline_revisions": stale_timelines,
            }
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'writer','BEAT_REPLAN_APPLIED','shot_group',?,'已应用选定 Beat 的 AI Replan',?)""",
                (actor, group_id, _json({**response, "draft_id": draft_id, "proposal_scene_no": proposal_scene_no})),
            )
            connection.execute(
                """INSERT INTO outbox_events (type,project_id,subject_type,subject_id,payload_json)
                VALUES ('episode.shot_plan.changed',?,'shot_group',?,?)""",
                (project_id, group_id, _json({"episode_id": episode_id, **response})),
            )
            connection.execute(
                """INSERT INTO command_idempotencies
                (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)""",
                (scope, idempotency_key, payload_hash, _json(response)),
            )
            return response

    def _build_plan(
        self, connection: sqlite3.Connection, *, episode_id: str, group_id: str, draft_id: str,
        proposal_scene_no: int, expected_group_revision: int,
    ) -> dict[str, Any]:
        group = connection.execute(
            """SELECT g.*,se.project_id FROM shot_groups g JOIN episodes e ON e.id=g.episode_id
            JOIN seasons se ON se.id=e.season_id WHERE g.id=? AND g.episode_id=?""",
            (group_id, episode_id),
        ).fetchone()
        if group is None:
            raise DomainRuleError("SHOT_GROUP_NOT_FOUND", "选定 Beat 不存在或不属于当前分集")
        if group["status"] != "ACTIVE":
            raise DomainRuleError("SHOT_GROUP_ARCHIVED", "已归档 Beat 不能应用 AI Replan")
        draft = connection.execute("SELECT * FROM script_breakdown_drafts WHERE id=?", (draft_id,)).fetchone()
        if draft is None or str(draft["project_id"]) != str(group["project_id"]):
            raise DomainRuleError("BREAKDOWN_DRAFT_NOT_FOUND", "AI 拆解草稿不存在或不属于当前项目")
        if str(draft["status"]) not in {"DRAFT_READY", "APPLIED"}:
            raise DomainRuleError("BREAKDOWN_DRAFT_NOT_READY", "AI 拆解草稿尚未准备好供人工审核")
        from local_drama.application.breakdown_revisions import load_effective_breakdown_draft

        payload, _effective_revision = load_effective_breakdown_draft(connection, draft)
        scenes = payload.get("scenes", []) if isinstance(payload, dict) else []
        scene = next((item for item in scenes if int(item.get("scene_no", -1)) == proposal_scene_no), None)
        if not isinstance(scene, dict):
            raise DomainRuleError("BREAKDOWN_SCENE_NOT_FOUND", "草稿中不存在选定的场次建议")
        proposals = [self._proposal(item, str(scene.get("summary") or "")) for item in scene.get("shots", [])]
        current = connection.execute(
            """SELECT s.*,sr.fields_json,sr.revision_no,sr.is_frozen FROM shot_group_members gm
            JOIN shots s ON s.id=gm.shot_id LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
            WHERE gm.group_id=? AND s.archived_at IS NULL ORDER BY gm.order_key,s.order_key,s.code,s.id""",
            (group_id,),
        ).fetchall()
        issues: list[dict[str, Any]] = []
        if int(group["revision"]) != expected_group_revision:
            issues.append({"code": "SHOT_GROUP_REVISION_CONFLICT", "message": "选定 Beat 已变化，请刷新"})
        diff: list[dict[str, Any]] = []
        paired = min(len(current), len(proposals))
        for index in range(paired):
            row, proposal = current[index], proposals[index]
            before = self._before(row)
            after = {**proposal, "code": str(row["code"]), "fields": {**before["fields"], **proposal["fields"]}}
            changed = self._comparable(before) != self._comparable(after)
            action = "PROTECTED" if changed and bool(row["is_frozen"]) else "MODIFY" if changed else "KEEP"
            diff.append({
                "action": action, "shot_id": row["id"], "expected_revision": int(row["revision"]),
                "current_revision_id": row["current_revision_id"], "current_revision_no": row["revision_no"],
                "before": before, "after": after,
                "reason": "冻结 revision 受到保护" if action == "PROTECTED" else None,
            })
        existing_codes = {str(row["code"]).casefold() for row in connection.execute(
            "SELECT code FROM shots WHERE episode_id=? AND archived_at IS NULL", (episode_id,),
        )}
        for proposal in proposals[paired:]:
            code = f"{group['code']}-AI-{int(proposal['shot_no']):02d}"
            if code.casefold() in existing_codes:
                issues.append({"code": "SHOT_CODE_CONFLICT", "message": f"建议镜头编号冲突：{code}"})
            existing_codes.add(code.casefold())
            diff.append({"action": "ADD", "shot_id": None, "expected_revision": None, "before": None,
                         "after": {**proposal, "code": code}, "reason": None})
        for row in current[paired:]:
            before = self._before(row)
            protected = bool(row["is_frozen"])
            diff.append({
                "action": "PROTECTED" if protected else "DELETE", "shot_id": row["id"],
                "expected_revision": int(row["revision"]), "current_revision_id": row["current_revision_id"],
                "current_revision_no": row["revision_no"], "before": before, "after": before if protected else None,
                "reason": "冻结 revision 不允许删除" if protected else "AI 建议从选定 Beat 移除；apply 只归档",
            })
        snapshot = [{
            "id": row["id"], "revision": row["revision"], "current_revision_id": row["current_revision_id"],
            "revision_no": row["revision_no"], "is_frozen": bool(row["is_frozen"]), "order_key": row["order_key"],
        } for row in current]
        canonical = {
            "episode_id": episode_id, "group_id": group_id, "group_revision": int(group["revision"]),
            "draft_id": draft_id, "draft_revision": int(draft["revision"]), "proposal_scene_no": proposal_scene_no,
            "snapshot": snapshot, "diff": diff,
        }
        counts = {action: sum(item["action"] == action for item in diff) for action in ("KEEP", "ADD", "MODIFY", "DELETE", "PROTECTED")}
        return {
            "episode_id": episode_id, "group_id": group_id, "group_code": group["code"], "group_title": group["title"],
            "group_revision": int(group["revision"]), "scene_id": group["scene_id"], "draft_id": draft_id,
            "proposal_scene_no": proposal_scene_no, "plan_hash": hashlib.sha256(_json(canonical).encode()).hexdigest(),
            "valid": not issues, "issues": issues, "diff": diff, "summary": counts,
            "scope": {"selected_group_only": True, "outside_group_shots_touched": 0},
        }

    @staticmethod
    def _proposal(item: dict[str, Any], summary: str) -> dict[str, Any]:
        duration_ms = max(1, int(float(item.get("duration_seconds") or 3) * 1000))
        shot_type = str(item.get("shot_type") or "STANDARD").strip().upper().replace(" ", "_")
        fields = {
            "visual": str(item.get("visual") or "").strip(), "action": str(item.get("action") or "").strip(),
            "dialogue": item.get("dialogue", ""), "summary": summary,
            "ai_replan_evidence": {"proposal_shot_no": int(item.get("shot_no") or 0)},
        }
        return {"shot_no": int(item.get("shot_no") or 0), "target_duration_ms": duration_ms,
                "shot_type": shot_type[:32] or "STANDARD", "fields": fields}

    @staticmethod
    def _before(row: sqlite3.Row) -> dict[str, Any]:
        try:
            fields = json.loads(str(row["fields_json"] or "{}"))
        except json.JSONDecodeError:
            fields = {}
        return {"code": row["code"], "target_duration_ms": int(row["target_duration_ms"]),
                "shot_type": row["shot_type"], "fields": fields if isinstance(fields, dict) else {}}

    @staticmethod
    def _comparable(value: dict[str, Any]) -> dict[str, Any]:
        fields = value["fields"]
        return {"target_duration_ms": value["target_duration_ms"], "shot_type": value["shot_type"],
                "fields": {key: fields.get(key) for key in ("visual", "action", "dialogue", "summary")}}

    @staticmethod
    def _mark_variants_stale(connection: sqlite3.Connection, shot_id: str, reason: str) -> None:
        connection.execute(
            """UPDATE generation_variants SET is_stale=1,stale_reason=?,updated_at=CURRENT_TIMESTAMP
            WHERE intent_id IN (SELECT id FROM generation_intents WHERE owner_type='SHOT' AND owner_id=?)""",
            (reason, shot_id),
        )

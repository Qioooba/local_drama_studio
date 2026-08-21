"""SQLite adapter for versioned QC policies and bounded reroll evidence."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.domain.errors import DomainRuleError


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _policy(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["policy"] = json.loads(str(item.pop("policy_json")))
    item["auto_reroll_categories"] = json.loads(str(item.pop("auto_reroll_categories_json")))
    item["is_frozen"] = bool(item["is_frozen"])
    return item


class SqliteQcPolicyRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def owner_project_id(self, owner_type: str, owner_id: str) -> str | None:
        if owner_type == "PROJECT":
            row = self.connection.execute("SELECT id AS project_id FROM projects WHERE id=?", (owner_id,)).fetchone()
        elif owner_type == "EPISODE":
            row = self.connection.execute(
                "SELECT se.project_id FROM episodes e JOIN seasons se ON se.id=e.season_id WHERE e.id=?", (owner_id,),
            ).fetchone()
        elif owner_type == "SHOT":
            row = self.connection.execute(
                """SELECT se.project_id FROM shots sh JOIN episodes e ON e.id=sh.episode_id
                JOIN seasons se ON se.id=e.season_id WHERE sh.id=?""", (owner_id,),
            ).fetchone()
        else:
            return None
        return str(row["project_id"]) if row else None

    def variant_context(self, variant_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT v.id, v.intent_id, i.project_id, i.owner_type, i.owner_id, pv.capability
            FROM generation_variants v JOIN generation_intents i ON i.id=v.intent_id
            LEFT JOIN execution_profile_versions pv ON pv.id=v.capability_profile_version_id
            WHERE v.id=?""", (variant_id,),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["shot_id"] = item["owner_id"] if item["owner_type"] == "SHOT" else None
        item["episode_id"] = item["owner_id"] if item["owner_type"] == "EPISODE" else None
        if item["shot_id"]:
            episode = self.connection.execute("SELECT episode_id FROM shots WHERE id=?", (item["shot_id"],)).fetchone()
            item["episode_id"] = str(episode["episode_id"]) if episode else None
        capability = str(item.get("capability") or "VIDEO").upper()
        item["stage"] = "AUDIO" if any(token in capability for token in ("AUDIO", "TTS", "VOICE")) else (
            "IMAGE" if "IMAGE" in capability else "VIDEO"
        )
        return item

    def shot_episode_id(self, shot_id: str) -> str | None:
        row = self.connection.execute("SELECT episode_id FROM shots WHERE id=?", (shot_id,)).fetchone()
        return str(row["episode_id"]) if row else None

    def machine_check(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM machine_check_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        subject_type, subject_id = str(item["subject_type"]), str(item["subject_id"])
        project_id: str | None = None
        if subject_type == "MEDIA_VERSION":
            project = self.connection.execute(
                """SELECT ma.project_id FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
                WHERE mv.id=?""", (subject_id,),
            ).fetchone()
            project_id = str(project["project_id"]) if project else None
        elif subject_type == "SHOT":
            project_id = self.owner_project_id("SHOT", subject_id)
        elif subject_type == "EPISODE":
            project_id = self.owner_project_id("EPISODE", subject_id)
        elif subject_type == "GENERATION_VARIANT":
            context = self.variant_context(subject_id)
            project_id = str(context["project_id"]) if context else None
        item["project_id"] = project_id
        return item

    def current_policy(self, project_id: str, owner_type: str, owner_id: str, stage: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT s.id AS policy_set_id,s.project_id,s.owner_type,s.owner_id,s.stage,s.status,s.revision,
            s.current_version_id AS policy_version_id,v.version_no,v.policy_json,v.max_auto_rerolls,
            v.auto_reroll_categories_json,v.is_frozen,v.reason,v.created_at,v.created_by
            FROM generation_qc_policy_sets s JOIN generation_qc_policy_versions v ON v.id=s.current_version_id
            WHERE s.project_id=? AND s.owner_type=? AND s.owner_id=? AND s.stage=? AND s.status='ACTIVE'""",
            (project_id, owner_type, owner_id, stage),
        ).fetchone()
        return _policy(row) if row else None

    def list_current(self, project_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT s.id AS policy_set_id,s.project_id,s.owner_type,s.owner_id,s.stage,s.status,s.revision,
            s.current_version_id AS policy_version_id,v.version_no,v.policy_json,v.max_auto_rerolls,
            v.auto_reroll_categories_json,v.is_frozen,v.reason,v.created_at,v.created_by
            FROM generation_qc_policy_sets s JOIN generation_qc_policy_versions v ON v.id=s.current_version_id
            WHERE s.project_id=? AND s.status='ACTIVE'
            ORDER BY s.stage,CASE s.owner_type WHEN 'PROJECT' THEN 1 WHEN 'EPISODE' THEN 2 ELSE 3 END,s.owner_id""",
            (project_id,),
        ).fetchall()
        return [_policy(row) for row in rows]

    def put_policy(
        self, *, project_id: str, owner_type: str, owner_id: str, stage: str,
        policy: dict[str, Any], max_auto_rerolls: int, auto_reroll_categories: list[str],
        reason: str, actor: str, expected_revision: int | None,
    ) -> dict[str, Any]:
        current = self.connection.execute(
            """SELECT id,revision FROM generation_qc_policy_sets
            WHERE project_id=? AND owner_type=? AND owner_id=? AND stage=?""",
            (project_id, owner_type, owner_id, stage),
        ).fetchone()
        now = _now()
        if current is None:
            if expected_revision is not None:
                raise DomainRuleError("QC_POLICY_REVISION_CONFLICT", "QC policy 尚不存在", {"actual_revision": None})
            set_id, revision, version_no = str(uuid.uuid4()), 1, 1
            self.connection.execute(
                """INSERT INTO generation_qc_policy_sets
                (id,project_id,owner_type,owner_id,stage,current_version_id,status,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,NULL,'ACTIVE',?,?,?,1,'v1')""",
                (set_id, project_id, owner_type, owner_id, stage, now, now, actor),
            )
        else:
            set_id, actual = str(current["id"]), int(current["revision"])
            if expected_revision != actual:
                raise DomainRuleError("QC_POLICY_REVISION_CONFLICT", "QC policy 已变化，请刷新后重试", {"expected_revision": expected_revision, "actual_revision": actual})
            revision = actual + 1
            version_no = int(self.connection.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 AS n FROM generation_qc_policy_versions WHERE policy_set_id=?", (set_id,),
            ).fetchone()["n"])
        version_id = str(uuid.uuid4())
        self.connection.execute(
            """INSERT INTO generation_qc_policy_versions
            (id,policy_set_id,version_no,policy_json,max_auto_rerolls,auto_reroll_categories_json,is_frozen,
             reason,created_at,created_by,schema_version) VALUES (?,?,?,?,?,?,1,?,?,?,'v1')""",
            (version_id, set_id, version_no, _json(policy), max_auto_rerolls, _json(auto_reroll_categories), reason, now, actor),
        )
        self.connection.execute(
            "UPDATE generation_qc_policy_sets SET current_version_id=?,updated_at=?,revision=? WHERE id=?",
            (version_id, now, revision, set_id),
        )
        self._audit(actor, "QC_POLICY_CHANGED", "generation_qc_policy_set", set_id, {
            "project_id": project_id, "owner_type": owner_type, "owner_id": owner_id, "stage": stage,
            "max_auto_rerolls": max_auto_rerolls, "categories": auto_reroll_categories,
        })
        result = self.current_policy(project_id, owner_type, owner_id, stage)
        assert result is not None
        return result

    def auto_retry_count(self, variant_id: str) -> int:
        row = self.connection.execute(
            """WITH RECURSIVE lineage(id,parent_variant_id,branch_reason) AS (
              SELECT id,parent_variant_id,branch_reason FROM generation_variants WHERE id=?
              UNION ALL
              SELECT p.id,p.parent_variant_id,p.branch_reason FROM generation_variants p
              JOIN lineage child ON child.parent_variant_id=p.id
            ) SELECT COUNT(*) AS n FROM lineage WHERE branch_reason LIKE 'QC_AUTO_RETRY%'""",
            (variant_id,),
        ).fetchone()
        return int(row["n"])

    def qc_link(self, variant_id: str, machine_check_run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM variant_qc_links WHERE variant_id=? AND machine_check_run_id=?",
            (variant_id, machine_check_run_id),
        ).fetchone()
        return dict(row) if row else None

    def record_qc_link(
        self, *, variant_id: str, machine_check_run_id: str, policy_version_id: str,
        category: str, disposition: str, retry_ordinal: int, reason: str, actor: str,
    ) -> dict[str, Any]:
        link_id, now = str(uuid.uuid4()), _now()
        self.connection.execute(
            """INSERT INTO variant_qc_links
            (id,variant_id,machine_check_run_id,policy_version_id,category,disposition,retry_ordinal,
             child_variant_id,reason,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,NULL,?,?,?,?,1,'v1')""",
            (link_id, variant_id, machine_check_run_id, policy_version_id, category, disposition, retry_ordinal, reason, now, now, actor),
        )
        self._audit(actor, "VARIANT_QC_DISPOSITION_RECORDED", "generation_variant", variant_id, {
            "machine_check_run_id": machine_check_run_id, "policy_version_id": policy_version_id,
            "category": category, "disposition": disposition, "retry_ordinal": retry_ordinal,
            "human_approval_created": False,
        })
        return dict(self.connection.execute("SELECT * FROM variant_qc_links WHERE id=?", (link_id,)).fetchone())

    def attach_child(self, link_id: str, parent_variant_id: str, child_variant_id: str, actor: str) -> dict[str, Any]:
        link = self.connection.execute("SELECT * FROM variant_qc_links WHERE id=?", (link_id,)).fetchone()
        if link is None:
            raise DomainRuleError("VARIANT_QC_LINK_NOT_FOUND", "Variant QC disposition 不存在")
        if str(link["variant_id"]) != parent_variant_id:
            raise DomainRuleError("VARIANT_QC_LINK_MISMATCH", "QC link 不属于当前候选")
        if str(link["disposition"]) != "AUTO_REROLL_ALLOWED":
            raise DomainRuleError("QC_AUTO_REROLL_NOT_ALLOWED", "当前 QC disposition 不允许自动重抽")
        if link["child_variant_id"] is not None:
            if str(link["child_variant_id"]) == child_variant_id:
                return dict(link)
            raise DomainRuleError("QC_AUTO_REROLL_ALREADY_ATTACHED", "该 QC 已关联另一个自动重抽候选")
        child = self.connection.execute(
            "SELECT parent_variant_id,branch_reason FROM generation_variants WHERE id=?", (child_variant_id,),
        ).fetchone()
        if child is None or str(child["parent_variant_id"] or "") != str(link["variant_id"]) or not str(child["branch_reason"]).startswith("QC_AUTO_RETRY"):
            raise DomainRuleError("QC_AUTO_REROLL_CHILD_INVALID", "自动重抽子候选 lineage 无效")
        now = _now()
        self.connection.execute(
            "UPDATE variant_qc_links SET child_variant_id=?,updated_at=?,revision=revision+1 WHERE id=?",
            (child_variant_id, now, link_id),
        )
        self._audit(actor, "QC_AUTO_REROLL_CHILD_ATTACHED", "generation_variant", child_variant_id, {
            "parent_variant_id": str(link["variant_id"]), "qc_link_id": link_id,
        })
        return dict(self.connection.execute("SELECT * FROM variant_qc_links WHERE id=?", (link_id,)).fetchone())

    def _audit(self, actor: str, action: str, subject_type: str, subject_id: str, metadata: dict[str, Any]) -> None:
        self.connection.execute(
            """INSERT INTO audit_events
            (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
            VALUES (?,'qc-agent',?,?,?,?,?)""",
            (actor, action, subject_type, subject_id, action.replace("_", " ").title(), _json(metadata)),
        )

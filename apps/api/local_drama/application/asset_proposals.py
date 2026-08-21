from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class AssetProposalService:
    """Resolve AI identity suggestions without destructive automatic merges."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def list(self, project_id: str, status: str | None = None) -> list[dict[str, Any]]:
        clause, params = (" AND p.status=?", [project_id, status]) if status else ("", [project_id])
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""SELECT p.*,suggested.code AS suggested_asset_code,suggested.name AS suggested_asset_name,
                resolved.code AS resolved_asset_code,resolved.name AS resolved_asset_name
                FROM story_asset_proposals p
                LEFT JOIN story_assets suggested ON suggested.id=p.suggested_asset_id
                LEFT JOIN story_assets resolved ON resolved.id=p.resolved_asset_id
                WHERE p.project_id=?{clause} ORDER BY p.created_at,p.id""", params,
            ).fetchall()
        return [self._item(row) for row in rows]

    def decide(
        self, proposal_id: str, *, action: str, expected_revision: int,
        target_asset_id: str | None = None, new_asset_code: str | None = None,
        decision_note: str = "", actor: str = "local-user",
    ) -> dict[str, Any]:
        if action not in {"CREATE_NEW", "MERGE_EXISTING", "REJECT"}:
            raise DomainRuleError("ASSET_PROPOSAL_ACTION_INVALID", "资产建议操作不受支持")
        now = _now()
        with self.database.transaction() as connection:
            proposal = connection.execute("SELECT * FROM story_asset_proposals WHERE id=?", (proposal_id,)).fetchone()
            if proposal is None:
                raise DomainRuleError("ASSET_PROPOSAL_NOT_FOUND", "资产建议不存在")
            if int(proposal["revision"]) != expected_revision:
                raise DomainRuleError("ASSET_PROPOSAL_REVISION_CONFLICT", "资产建议已被处理，请刷新")
            if str(proposal["status"]) != "PENDING":
                raise DomainRuleError("ASSET_PROPOSAL_ALREADY_DECIDED", "资产建议已经处理")
            resolved_id: str | None = None
            status = "REJECTED"
            if action == "MERGE_EXISTING":
                resolved_id = target_asset_id or proposal["suggested_asset_id"]
                asset = connection.execute(
                    "SELECT * FROM story_assets WHERE id=? AND project_id=? AND kind=? AND status='ACTIVE'",
                    (resolved_id, proposal["project_id"], proposal["kind"]),
                ).fetchone()
                if asset is None:
                    raise DomainRuleError("ASSET_PROPOSAL_TARGET_INVALID", "合并目标不存在、已归档或类型不一致")
                status = "ACCEPTED_MERGE"
            elif action == "CREATE_NEW":
                code = str(new_asset_code or "").strip().upper()
                if not re.fullmatch(r"[A-Z][A-Z0-9_-]{1,119}", code):
                    raise DomainRuleError("STORY_ASSET_CODE_INVALID", "新资产 code 必须为 2—120 位大写 ASCII、数字、下划线或连字符")
                resolved_id = str(uuid.uuid4())
                try:
                    connection.execute(
                        """INSERT INTO story_assets
                        (id,project_id,kind,code,name,description,canonical_media_version_id,extra_json,status,
                         created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,'',NULL,?,'ACTIVE',?,?,?,1,'v2')""",
                        (resolved_id, proposal["project_id"], proposal["kind"], code, proposal["name"],
                         _json({"source_asset_proposal_id": proposal_id}), now, now, actor),
                    )
                except sqlite3.IntegrityError as error:
                    raise DomainRuleError("STORY_ASSET_CODE_CONFLICT", "同一项目内的资产 code 已存在") from error
                connection.execute(
                    """INSERT INTO audit_events
                    (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                    VALUES (?,'writer','STORY_ASSET_CREATED','story_asset',?,'接受 AI 建议并创建故事资产',?)""",
                    (actor, resolved_id, _json({"proposal_id": proposal_id, "code": code})),
                )
                status = "ACCEPTED_NEW"
            connection.execute(
                """UPDATE story_asset_proposals SET status=?,resolved_asset_id=?,decision_note=?,updated_at=?,
                revision=revision+1 WHERE id=? AND revision=?""",
                (status, resolved_id, decision_note.strip(), now, proposal_id, expected_revision),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,before_revision,after_revision,summary,metadata_redacted_json)
                VALUES (?,'writer','STORY_ASSET_PROPOSAL_DECIDED','story_asset_proposal',?,?,?,'人工处理资产身份建议',?)""",
                (actor, proposal_id, expected_revision, expected_revision + 1,
                 _json({"action": action, "resolved_asset_id": resolved_id, "non_destructive_merge": True})),
            )
        return next(item for item in self.list(str(proposal["project_id"])) if item["id"] == proposal_id)

    @staticmethod
    def _item(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["evidence"] = json.loads(str(item.pop("evidence_json")))
        return item

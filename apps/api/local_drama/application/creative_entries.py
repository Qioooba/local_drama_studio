"""Immutable creative bible and asset text revision workflow."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

KINDS = {"SERIES_BIBLE", "CHARACTER", "SCENE", "PROP", "COSTUME", "STYLE", "VOICE"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(content: dict[str, Any]) -> str:
    return hashlib.sha256(_json(content).encode("utf-8")).hexdigest()


def _validate(kind: str, code: str, title: str, content: dict[str, Any], change_note: str) -> None:
    if kind not in KINDS:
        raise DomainRuleError("CREATIVE_ENTRY_KIND_INVALID", "创作资料 kind 不受支持", {"allowed": sorted(KINDS)})
    if not re.fullmatch(r"[A-Z][A-Z0-9_-]{1,63}", code):
        raise DomainRuleError("CREATIVE_ENTRY_CODE_INVALID", "创作资料 code 必须是 2—64 位大写 ASCII、数字、下划线或连字符")
    if not title.strip() or not content or not change_note.strip():
        raise DomainRuleError("CREATIVE_ENTRY_FIELDS_REQUIRED", "标题、结构化内容和变更说明必填")


class CreativeEntryService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, project_id: str, kind: str, code: str, title: str, content: dict[str, Any], change_note: str, actor: str = "local-user") -> dict[str, Any]:
        _validate(kind, code, title, content, change_note)
        entry_id, revision_id, now = str(uuid.uuid4()), str(uuid.uuid4()), _now()
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
            try:
                connection.execute(
                    """INSERT INTO creative_entries (id,project_id,kind,code,title,current_revision_id,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,?,NULL,?,?,?,1,'v2')""",
                    (entry_id, project_id, kind, code, title.strip(), now, now, actor),
                )
            except sqlite3.IntegrityError as error:
                raise DomainRuleError("CREATIVE_ENTRY_CODE_CONFLICT", "同项目同类型 code 已存在") from error
            connection.execute(
                """INSERT INTO creative_entry_revisions (id,entry_id,revision_no,parent_revision_id,restored_from_revision_id,content_json,content_hash,change_note,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,1,NULL,NULL,?,?,?,?,?,?,1,'v2')""",
                (revision_id, entry_id, _json(content), _digest(content), change_note.strip(), now, now, actor),
            )
            connection.execute("UPDATE creative_entries SET current_revision_id=? WHERE id=?", (revision_id, entry_id))
        return self.get(entry_id)

    def save_revision(self, entry_id: str, content: dict[str, Any], change_note: str, actor: str = "local-user", restored_from_revision_id: str | None = None) -> dict[str, Any]:
        if not content or not change_note.strip():
            raise DomainRuleError("CREATIVE_ENTRY_FIELDS_REQUIRED", "结构化内容和变更说明必填")
        revision_id, now = str(uuid.uuid4()), _now()
        with self.database.transaction() as connection:
            entry = connection.execute("SELECT * FROM creative_entries WHERE id=?", (entry_id,)).fetchone()
            if entry is None:
                raise DomainRuleError("CREATIVE_ENTRY_NOT_FOUND", "创作资料不存在")
            current = connection.execute("SELECT * FROM creative_entry_revisions WHERE id=?", (entry["current_revision_id"],)).fetchone()
            if current is not None and current["content_hash"] == _digest(content):
                raise DomainRuleError("CREATIVE_REVISION_UNCHANGED", "新 revision 必须产生内容变化")
            if restored_from_revision_id:
                source = connection.execute("SELECT * FROM creative_entry_revisions WHERE id=? AND entry_id=?", (restored_from_revision_id, entry_id)).fetchone()
                if source is None:
                    raise DomainRuleError("CREATIVE_REVISION_SCOPE_INVALID", "回退源 revision 不属于当前资料")
            next_no = int(connection.execute("SELECT COALESCE(MAX(revision_no),0)+1 FROM creative_entry_revisions WHERE entry_id=?", (entry_id,)).fetchone()[0])
            connection.execute(
                """INSERT INTO creative_entry_revisions (id,entry_id,revision_no,parent_revision_id,restored_from_revision_id,content_json,content_hash,change_note,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,1,'v2')""",
                (revision_id, entry_id, next_no, entry["current_revision_id"], restored_from_revision_id, _json(content), _digest(content), change_note.strip(), now, now, actor),
            )
            connection.execute("UPDATE creative_entries SET current_revision_id=?,revision=revision+1,updated_at=? WHERE id=?", (revision_id, now, entry_id))
        return self.get_revision(revision_id)

    def restore(self, entry_id: str, revision_id: str, change_note: str, actor: str = "local-user") -> dict[str, Any]:
        source = self.get_revision(revision_id)
        if source["entry_id"] != entry_id:
            raise DomainRuleError("CREATIVE_REVISION_SCOPE_INVALID", "回退源 revision 不属于当前资料")
        return self.save_revision(entry_id, source["content"], change_note, actor, restored_from_revision_id=revision_id)

    def list_entries(self, project_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        params: list[str] = [project_id]
        clause = " AND ce.kind=?" if kind else ""
        if kind:
            params.append(kind)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""SELECT ce.*,cer.revision_no,cer.content_hash,cer.change_note,cer.content_json
                FROM creative_entries ce JOIN creative_entry_revisions cer ON cer.id=ce.current_revision_id
                WHERE ce.project_id=?{clause} ORDER BY ce.kind,ce.code""",
                params,
            ).fetchall()
        return [self._entry(row) for row in rows]

    def get(self, entry_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT ce.*,cer.revision_no,cer.content_hash,cer.change_note,cer.content_json
                FROM creative_entries ce JOIN creative_entry_revisions cer ON cer.id=ce.current_revision_id WHERE ce.id=?""",
                (entry_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("CREATIVE_ENTRY_NOT_FOUND", "创作资料不存在")
        return self._entry(row)

    def revisions(self, entry_id: str) -> list[dict[str, Any]]:
        self.get(entry_id)
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM creative_entry_revisions WHERE entry_id=? ORDER BY revision_no DESC", (entry_id,)).fetchall()
        return [self._revision(row) for row in rows]

    def get_revision(self, revision_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM creative_entry_revisions WHERE id=?", (revision_id,)).fetchone()
        if row is None:
            raise DomainRuleError("CREATIVE_REVISION_NOT_FOUND", "创作资料 revision 不存在")
        return self._revision(row)

    def compare(self, entry_id: str, left_id: str, right_id: str) -> dict[str, Any]:
        left, right = self.get_revision(left_id), self.get_revision(right_id)
        if left["entry_id"] != entry_id or right["entry_id"] != entry_id:
            raise DomainRuleError("CREATIVE_REVISION_SCOPE_INVALID", "比较 revision 必须属于同一资料")
        keys = sorted(set(left["content"]) | set(right["content"]))
        return {"entry_id": entry_id, "left": left, "right": right, "changes": [{"field": key, "before": left["content"].get(key), "after": right["content"].get(key)} for key in keys if left["content"].get(key) != right["content"].get(key)], "read_only": True}

    @staticmethod
    def _entry(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["content"] = json.loads(item.pop("content_json"))
        return item

    @staticmethod
    def _revision(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["content"] = json.loads(item.pop("content_json"))
        return item

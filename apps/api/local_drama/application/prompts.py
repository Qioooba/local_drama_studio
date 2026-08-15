"""Immutable prompt revision commands used by generation branches."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(content_text: str, structured: dict[str, Any]) -> str:
    return hashlib.sha256(_json({"content_text": content_text, "structured": structured}).encode("utf-8")).hexdigest()


def _validate_template(content_text: str, structured: dict[str, Any]) -> None:
    required = ("source_fields", "template_text", "expanded_text", "negative_text", "language", "model_profile_version_id")
    missing = [key for key in required if key not in structured or (key not in {"negative_text", "source_fields"} and not structured[key])]
    if missing or not isinstance(structured.get("source_fields"), dict):
        raise DomainRuleError("PROMPT_TEMPLATE_FIELDS_REQUIRED", "提示词模板缺少结构化字段", {"missing_fields": missing})
    if content_text != structured["expanded_text"]:
        raise DomainRuleError("PROMPT_EXPANSION_MISMATCH", "冻结内容必须等于结构化展开结果")


class PromptService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_prompt(
        self,
        project_id: str,
        owner_type: str,
        owner_id: str,
        purpose: str,
        title: str,
        content_text: str,
        structured: dict[str, Any] | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if not content_text.strip():
            raise DomainRuleError("PROMPT_CONTENT_REQUIRED", "Prompt 内容不能为空")
        frozen = structured or {}
        if purpose == "GENERATION_TEMPLATE":
            _validate_template(content_text, frozen)
        prompt_id = str(uuid.uuid4())
        revision_id = str(uuid.uuid4())
        now = _now()
        with self.database.transaction() as connection:
            if connection.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
            connection.execute(
                """INSERT INTO prompts
                (id, project_id, owner_type, owner_id, purpose, title, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'v2')""",
                (prompt_id, project_id, owner_type, owner_id, purpose, title, now, now, actor),
            )
            connection.execute(
                """INSERT INTO prompt_revisions
                (id, prompt_id, revision_no, parent_revision_id, content_text, structured_json, content_hash, status,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, 1, NULL, ?, ?, ?, 'FROZEN', ?, ?, ?, 1, 'v2')""",
                (revision_id, prompt_id, content_text, _json(frozen), _hash(content_text, frozen), now, now, actor),
            )
        return {"prompt": self.get_prompt(prompt_id), "revision": self.get_revision(revision_id)}

    def list_prompts(self, project_id: str, owner_type: str | None = None, owner_id: str | None = None) -> list[dict[str, Any]]:
        clauses = ["p.project_id=?"]
        params: list[str] = [project_id]
        if owner_type:
            clauses.append("p.owner_type=?")
            params.append(owner_type)
        if owner_id:
            clauses.append("p.owner_id=?")
            params.append(owner_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""SELECT p.*,pr.id AS revision_id,pr.revision_no,pr.parent_revision_id,pr.content_text,
                pr.structured_json,pr.content_hash,pr.status AS revision_status
                FROM prompts p JOIN prompt_revisions pr ON pr.prompt_id=p.id
                WHERE {' AND '.join(clauses)} AND pr.revision_no=(SELECT MAX(latest.revision_no) FROM prompt_revisions latest WHERE latest.prompt_id=p.id)
                ORDER BY p.updated_at DESC,p.id""",
                params,
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["structured"] = json.loads(item.pop("structured_json"))
            items.append(item)
        return items

    def branch_revision(
        self,
        parent_revision_id: str,
        content_text: str,
        structured: dict[str, Any] | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if not content_text.strip():
            raise DomainRuleError("PROMPT_CONTENT_REQUIRED", "Prompt 内容不能为空")
        frozen = structured or {}
        revision_id = str(uuid.uuid4())
        now = _now()
        with self.database.transaction() as connection:
            parent = connection.execute(
                """SELECT pr.*,p.purpose FROM prompt_revisions pr JOIN prompts p ON p.id=pr.prompt_id WHERE pr.id=?""",
                (parent_revision_id,),
            ).fetchone()
            if parent is None:
                raise DomainRuleError("PROMPT_REVISION_NOT_FOUND", "父 PromptRevision 不存在")
            if parent["purpose"] == "GENERATION_TEMPLATE":
                _validate_template(content_text, frozen)
            content_hash = _hash(content_text, frozen)
            if content_hash == parent["content_hash"]:
                raise DomainRuleError("PROMPT_BRANCH_UNCHANGED", "Prompt branch 必须产生内容变化")
            next_no = int(
                connection.execute(
                    "SELECT COALESCE(MAX(revision_no), 0) + 1 AS next_no FROM prompt_revisions WHERE prompt_id=?",
                    (parent["prompt_id"],),
                ).fetchone()["next_no"]
            )
            connection.execute(
                """INSERT INTO prompt_revisions
                (id, prompt_id, revision_no, parent_revision_id, content_text, structured_json, content_hash, status,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'FROZEN', ?, ?, ?, 1, 'v2')""",
                (revision_id, parent["prompt_id"], next_no, parent_revision_id, content_text, _json(frozen), content_hash, now, now, actor),
            )
        return self.get_revision(revision_id)

    def get_prompt(self, prompt_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM prompts WHERE id=?", (prompt_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROMPT_NOT_FOUND", "Prompt 不存在")
        return dict(row)

    def get_revision(self, revision_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT pr.*, p.project_id, p.owner_type, p.owner_id, p.purpose FROM prompt_revisions pr
                JOIN prompts p ON p.id=pr.prompt_id WHERE pr.id=?""",
                (revision_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("PROMPT_REVISION_NOT_FOUND", "PromptRevision 不存在")
        result = dict(row)
        result["structured"] = json.loads(result.pop("structured_json"))
        return result

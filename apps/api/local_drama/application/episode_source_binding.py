"""Resolve one episode's immutable source authority without guessing latest imports."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.path_policy import controlled_path


def _decode(raw: object) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def resolve_episode_source_binding(
    connection: sqlite3.Connection, episode_id: str
) -> dict[str, Any]:
    episode = connection.execute(
        """SELECT e.id,e.source_range_json,s.project_id,p.root_rel
        FROM episodes e JOIN seasons s ON s.id=e.season_id
        JOIN projects p ON p.id=s.project_id WHERE e.id=?""",
        (episode_id,),
    ).fetchone()
    if episode is None:
        raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
    scope = _decode(episode["source_range_json"])
    start = scope.get("start_paragraph") or scope.get("source_paragraph_start")
    end = scope.get("end_paragraph") or scope.get("source_paragraph_end")

    source_version_id = str(scope.get("source_document_version_id") or "").strip()
    import_session_id = str(scope.get("import_session_id") or "").strip()
    resolution = "EXPLICIT_EPISODE_BINDING"
    if not source_version_id:
        rows = connection.execute(
            """SELECT DISTINCT d.source_document_version_id,d.import_session_id
            FROM script_breakdown_drafts d
            JOIN audit_events ae ON ae.subject_type='script_breakdown_draft'
              AND ae.subject_id=d.id AND ae.action='SCRIPT_BREAKDOWN_APPLIED'
            WHERE d.project_id=? AND json_extract(ae.metadata_redacted_json,'$.episode_id')=?""",
            (str(episode["project_id"]), episode_id),
        ).fetchall()
        candidates = {
            (str(row["source_document_version_id"]), str(row["import_session_id"]))
            for row in rows
            if row["source_document_version_id"] and row["import_session_id"]
        }
        if not candidates:
            rows = connection.execute(
                """SELECT DISTINCT i.source_document_version_id,i.id AS import_session_id
                FROM import_sessions i WHERE i.project_id=?
                AND EXISTS (SELECT 1 FROM audit_events ae
                  WHERE ae.action='IMPORT_SESSION_COMMITTED'
                  AND ae.subject_type='import_session' AND ae.subject_id=i.id)""",
                (str(episode["project_id"]),),
            ).fetchall()
            candidates = {
                (str(row["source_document_version_id"]), str(row["import_session_id"]))
                for row in rows
            }
        if len(candidates) != 1:
            raise DomainRuleError(
                "EPISODE_SOURCE_BINDING_AMBIGUOUS" if candidates else "EPISODE_SOURCE_COMMIT_REQUIRED",
                "旧分集存在多份候选原稿，请先明确绑定。" if candidates else "没有已确认提交的原文，无法解析本集来源。",
                {"episode_id": episode_id, "candidate_count": len(candidates)},
            )
        source_version_id, import_session_id = next(iter(candidates))
        resolution = "UNIQUE_LEGACY_EVIDENCE"

    source = connection.execute(
        """SELECT v.id,v.text_sha256,v.extracted_text_rel,v.parse_status,v.source_document_id
        FROM source_document_versions v JOIN source_documents d ON d.id=v.source_document_id
        WHERE v.id=? AND d.project_id=?""",
        (source_version_id, str(episode["project_id"])),
    ).fetchone()
    if source is None:
        raise DomainRuleError(
            "EPISODE_SOURCE_VERSION_NOT_FOUND",
            "本集绑定的原稿版本不存在或不属于当前项目。",
            {"source_document_version_id": source_version_id},
        )
    committed = connection.execute(
        """SELECT i.id FROM import_sessions i WHERE i.id=? AND i.project_id=?
        AND i.source_document_version_id=? AND EXISTS (
          SELECT 1 FROM audit_events ae WHERE ae.action='IMPORT_SESSION_COMMITTED'
          AND ae.subject_type='import_session' AND ae.subject_id=i.id)""",
        (import_session_id, str(episode["project_id"]), source_version_id),
    ).fetchone()
    if committed is None:
        raise DomainRuleError(
            "EPISODE_SOURCE_IMPORT_MISMATCH", "本集绑定的导入会话未提交或与原稿版本不一致。"
        )
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
        body_range_row = connection.execute(
            """SELECT payload_json FROM import_session_items
            WHERE session_id=? AND item_type='SOURCE_BODY_RANGE' AND validation_status='VALID'
            ORDER BY created_at DESC,id DESC LIMIT 1""",
            (import_session_id,),
        ).fetchone()
        body_range = _decode(body_range_row["payload_json"]) if body_range_row else {}
        start = body_range.get("source_paragraph_start")
        end = body_range.get("source_paragraph_end")
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            raise DomainRuleError(
                "EPISODE_SOURCE_RANGE_REQUIRED",
                "本集还没有已确认的原文范围，请先完成分集大纲。",
            )
        resolution = f"{resolution}_WITH_COMMITTED_IMPORT_RANGE"
    bound_hash = str(scope.get("text_sha256") or "").strip()
    stored_hash = str(source["text_sha256"] or "")
    if bound_hash and bound_hash != stored_hash:
        raise DomainRuleError(
            "EPISODE_SOURCE_HASH_MISMATCH",
            "本集保存的原稿指纹与源版本记录不一致。",
            {"bound_text_sha256": bound_hash, "stored_text_sha256": stored_hash},
        )
    return {
        "episode_id": episode_id,
        "project_id": str(episode["project_id"]),
        "project_root_rel": str(episode["root_rel"]),
        "source_document_version_id": source_version_id,
        "import_session_id": import_session_id,
        "text_sha256": stored_hash,
        "extracted_text_rel": str(source["extracted_text_rel"] or ""),
        "parse_status": str(source["parse_status"] or ""),
        "start_paragraph": start,
        "end_paragraph": end,
        "resolution": resolution,
    }


def validate_episode_source_binding(settings: Settings, binding: dict[str, Any]) -> None:
    """Verify the bound immutable extracted text before any new source-derived work."""
    if str(binding.get("parse_status") or "") != "PARSED":
        raise DomainRuleError(
            "EPISODE_SOURCE_NOT_PARSED", "本集绑定的原稿版本尚未完成可靠解析。"
        )
    project_root = settings.resolve_project_root(str(binding["project_root_rel"]))
    path = controlled_path(
        project_root,
        str(binding.get("extracted_text_rel") or ""),
        must_exist=True,
        require_file=True,
        code="EPISODE_SOURCE_TEXT_INVALID",
    )
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise DomainRuleError(
            "EPISODE_SOURCE_TEXT_INVALID", "本集绑定的不可变原稿无法读取。"
        ) from error
    if digest != str(binding.get("text_sha256") or ""):
        raise DomainRuleError(
            "EPISODE_SOURCE_TEXT_CHANGED",
            "本集绑定的不可变原稿内容已变化，已停止继续处理。",
            {"source_document_version_id": binding.get("source_document_version_id")},
        )

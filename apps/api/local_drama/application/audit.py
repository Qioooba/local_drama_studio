"""Read-only, local audit history queries.

Audit events are append-only records written by the domain services.  This
module intentionally does not expose a mutation endpoint: the only supported
operation is a bounded, stable-cursor read.  The database predates a
``project_id`` column on ``audit_events`` so project scope is resolved from
the immutable subject graph (and from the metadata project hint when one is
present).  No network or runtime is contacted.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from local_drama.infrastructure.database.sqlite import Database

_REDACTED = "[REDACTED]"
_REDACTED_PATH = "[LOCAL_PATH_REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?:token|secret|password|passwd|api[_-]?key|authorization|cookie|credential|private[_-]?key|machine[_-]?path|path[_-]?ref)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)(bearer\s+)[^\s,;]+|((?:(?:access|refresh|session)[_-]?token|client[_-]?secret|token|secret|password|passwd|api[_-]?key|authorization)\s*[:=]\s*)[^\s,;]+"
)
_LOCAL_PATH = re.compile(r"(?:(?:[A-Za-z]:[\\/])|(?:^|\s)(?:[^\s,;\"']+[\\/]))[^\s,;\"']*")


def _redact(value: Any, *, key: str | None = None) -> Any:
    """Defensively redact secrets and local file references recursively."""

    if key and _SENSITIVE_KEY.search(key):
        return _REDACTED_PATH if "path" in key.lower() else _REDACTED
    if isinstance(value, dict):
        return {str(name): _redact(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        scrubbed = _SENSITIVE_TEXT.sub(lambda match: f"{match.group(1) or match.group(2) or ''}{_REDACTED}", value)
        return _LOCAL_PATH.sub(_REDACTED_PATH, scrubbed)
    return value


def _safe_metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"value": _REDACTED}
    redacted = _redact(parsed)
    return redacted if isinstance(redacted, dict) else {"value": redacted}


def _time_value(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return value.replace("T", " ").replace("Z", "")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    text = parsed.isoformat(sep=" ")
    # SQLite's CURRENT_TIMESTAMP is ``YYYY-MM-DD HH:MM:SS``.  Keep ordering
    # lexical and accept the ISO ``T``/``Z`` spelling used by the web client.
    return text.replace("T", " ").replace("Z", "")


# Subject-to-project resolution.  Every relation references immutable source
# entities; this CTE is read-only and deliberately excludes global Runtime /
# Profile records unless their audit subject itself is a project-owned row.
_PROJECT_SUBJECTS = """
WITH project_subjects(subject_type, subject_id, project_id) AS (
    SELECT 'project', id, id FROM projects
    UNION ALL SELECT 'season', id, project_id FROM seasons
    UNION ALL SELECT 'scene', id, project_id FROM scenes
    UNION ALL SELECT 'episode', e.id, s.project_id FROM episodes e JOIN seasons s ON s.id = e.season_id
    UNION ALL SELECT 'shot', sh.id, s.project_id FROM shots sh JOIN episodes e ON e.id = sh.episode_id JOIN seasons s ON s.id = e.season_id
    UNION ALL SELECT 'shot_revision', sr.id, s.project_id FROM shot_revisions sr JOIN shots sh ON sh.id = sr.shot_id JOIN episodes e ON e.id = sh.episode_id JOIN seasons s ON s.id = e.season_id
    UNION ALL SELECT 'media_asset', id, project_id FROM media_assets
    UNION ALL SELECT 'media_version', mv.id, ma.project_id FROM media_versions mv JOIN media_assets ma ON ma.id = mv.media_asset_id
    UNION ALL SELECT 'selection', se.id, ma.project_id FROM selections se JOIN media_assets ma ON ma.id = se.media_asset_id
    UNION ALL SELECT 'job', id, project_id FROM jobs
    UNION ALL SELECT 'job_attempt', ja.id, j.project_id FROM job_attempts ja JOIN jobs j ON j.id = ja.job_id
    UNION ALL SELECT 'artifact', a.id, j.project_id FROM artifacts a JOIN job_attempts ja ON ja.id = a.job_attempt_id JOIN jobs j ON j.id = ja.job_id
    UNION ALL SELECT 'generation_intent', id, project_id FROM generation_intents
    UNION ALL SELECT 'generation_variant', gv.id, gi.project_id FROM generation_variants gv JOIN generation_intents gi ON gi.id = gv.intent_id
    UNION ALL SELECT 'generation_experiment', ge.id, gi.project_id FROM generation_experiments ge JOIN generation_intents gi ON gi.id = ge.intent_id
    UNION ALL SELECT 'prompt', id, project_id FROM prompts
    UNION ALL SELECT 'prompt_revision', pr.id, p.project_id FROM prompt_revisions pr JOIN prompts p ON p.id = pr.prompt_id
    UNION ALL SELECT 'source_document', id, project_id FROM source_documents
    UNION ALL SELECT 'source_document_version', sdv.id, sd.project_id FROM source_document_versions sdv JOIN source_documents sd ON sd.id = sdv.source_document_id
    UNION ALL SELECT 'import_session', id, project_id FROM import_sessions
    UNION ALL SELECT 'script_breakdown_draft', id, project_id FROM script_breakdown_drafts
    UNION ALL SELECT 'timeline_revision', tr.id, s.project_id FROM timeline_revisions tr JOIN episodes e ON e.id = tr.episode_id JOIN seasons s ON s.id = e.season_id
    UNION ALL SELECT 'subtitle_revision', sr.id, s.project_id FROM subtitle_revisions sr JOIN episodes e ON e.id = sr.episode_id JOIN seasons s ON s.id = e.season_id
    UNION ALL SELECT 'audio_binding', ab.id, s.project_id FROM audio_bindings ab JOIN episodes e ON e.id = ab.episode_id JOIN seasons s ON s.id = e.season_id
    UNION ALL SELECT 'episode_render_version', erv.id, s.project_id FROM episode_render_versions erv JOIN episodes e ON e.id = erv.episode_id JOIN seasons s ON s.id = e.season_id
    UNION ALL SELECT 'delivery_package', dp.id, s.project_id FROM delivery_packages dp JOIN episode_render_versions erv ON erv.id = dp.episode_render_version_id JOIN episodes e ON e.id = erv.episode_id JOIN seasons s ON s.id = e.season_id
    UNION ALL SELECT 'delivery_target', id, project_id FROM delivery_targets
    UNION ALL SELECT 'delivery_target_version', dtv.id, dt.project_id FROM delivery_target_versions dtv JOIN delivery_targets dt ON dt.id = dtv.delivery_target_id
    UNION ALL SELECT 'automation_workflow', id, project_id FROM automation_workflows
    UNION ALL SELECT 'automation_workflow_run', id, project_id FROM automation_workflow_runs
    UNION ALL SELECT 'automation_client', id, project_id FROM automation_clients WHERE project_id IS NOT NULL
    UNION ALL SELECT 'webhook_subscription', id, project_id FROM webhook_subscriptions WHERE project_id IS NOT NULL
    UNION ALL SELECT 'outbox', event_id, project_id FROM outbox_events WHERE project_id IS NOT NULL
    UNION ALL SELECT 'brand_kit', id, project_id FROM brand_kits
    UNION ALL SELECT 'watermark_profile', id, project_id FROM watermark_profiles
    UNION ALL SELECT 'compliance_policy', id, project_id FROM compliance_policies
    UNION ALL SELECT 'creative_entry', id, project_id FROM creative_entries
    UNION ALL SELECT 'voice_profile_version', id, project_id FROM voice_profile_versions
    UNION ALL SELECT 'workspace_asset_authorization', id, project_id FROM workspace_asset_authorizations
    UNION ALL SELECT 'model_license_evidence', id, project_id FROM model_license_evidence
    UNION ALL SELECT 'canvas_layout', id, project_id FROM canvas_layouts
    UNION ALL SELECT 'canvas_execution_plan', id, project_id FROM canvas_execution_plans
    UNION ALL SELECT 'review_batch_plan', id, project_id FROM review_batch_plans
    UNION ALL SELECT 'g7_network_e2e_attestation', id, project_id FROM g7_network_e2e_attestations
)
"""


class AuditService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_page(
        self,
        *,
        project_id: str | None = None,
        occurred_after: datetime | str | None = None,
        occurred_before: datetime | str | None = None,
        action: str | None = None,
        actor: str | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        cursor: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 100))
        bounded_cursor = max(0, int(cursor))
        clauses = ["e.event_id < ?"]
        params: list[Any] = [bounded_cursor if bounded_cursor > 0 else 9223372036854775807]
        if project_id:
            clauses.append("(ps.project_id = ? OR (json_valid(e.metadata_redacted_json) AND json_extract(e.metadata_redacted_json, '$.project_id') = ?))")
            params.extend([project_id, project_id])
        if (value := _time_value(occurred_after)) is not None:
            clauses.append("e.occurred_at >= ?")
            params.append(value)
        if (value := _time_value(occurred_before)) is not None:
            clauses.append("e.occurred_at <= ?")
            params.append(value)
        for column, value in (("action", action), ("actor", actor), ("subject_type", subject_type), ("subject_id", subject_id)):
            if value:
                clauses.append(f"e.{column} = ?")
                params.append(value)
        sql = (
            _PROJECT_SUBJECTS
            + "SELECT e.*, ps.project_id AS resolved_project_id FROM audit_events e "
            + "LEFT JOIN project_subjects ps ON ps.subject_type = e.subject_type AND ps.subject_id = e.subject_id "
            + "WHERE "
            + " AND ".join(clauses)
            + " ORDER BY e.event_id DESC LIMIT ?"
        )
        params.append(bounded_limit + 1)
        with self.database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        has_more = len(rows) > bounded_limit
        rows = rows[:bounded_limit]
        items: list[dict[str, Any]] = []
        for row in rows:
            item = {
                "event_id": int(row["event_id"]),
                "actor": _redact(str(row["actor"])),
                "role_context": str(row["role_context"]),
                "action": str(row["action"]),
                "subject_type": str(row["subject_type"]),
                "subject_id": str(row["subject_id"]),
                "project_id": row["resolved_project_id"],
                "before_revision": row["before_revision"],
                "after_revision": row["after_revision"],
                "request_id": row["request_id"],
                "job_id": row["job_id"],
                "occurred_at": str(row["occurred_at"]),
                "summary": _redact(str(row["summary"])),
                "metadata": _safe_metadata(row["metadata_redacted_json"]),
                "metadata_redacted": True,
                "local_only": True,
                "network_contacted": False,
                "mutated": False,
            }
            items.append(item)
        next_cursor = int(items[-1]["event_id"]) if has_more and items else None
        return {
            "items": items,
            "next_cursor": next_cursor,
            "cursor": bounded_cursor,
            "limit": bounded_limit,
            "filters": {
                "project_id": project_id,
                "occurred_after": _time_value(occurred_after),
                "occurred_before": _time_value(occurred_before),
                "action": action,
                "actor": actor,
                "subject_type": subject_type,
                "subject_id": subject_id,
            },
            "local_only": True,
            "network_contacted": False,
            "mutated": False,
        }

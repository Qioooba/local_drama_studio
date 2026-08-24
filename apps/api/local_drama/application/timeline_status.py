"""Read-only G8 timeline and delivery status projection."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TimelineStatusService:
    """Summarize persisted episode state without rendering or mutating it."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def inspect(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            episode = connection.execute(
                "SELECT e.id, e.code, e.title, s.project_id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE e.id=?",
                (episode_id,),
            ).fetchone()
            if episode is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
            timeline = connection.execute(
                """SELECT tr.id, tr.revision_no, tr.status, tr.created_at,
                (SELECT COUNT(*) FROM timeline_items ti WHERE ti.timeline_revision_id=tr.id) AS item_count
                FROM timeline_revisions tr WHERE tr.episode_id=? ORDER BY tr.revision_no DESC LIMIT 1""", (episode_id,)
            ).fetchone()
            latest_frozen_timeline = connection.execute(
                """SELECT tr.id, tr.revision_no, tr.status, tr.created_at,
                (SELECT COUNT(*) FROM timeline_items ti WHERE ti.timeline_revision_id=tr.id) AS item_count
                FROM timeline_revisions tr WHERE tr.episode_id=? AND tr.status='FROZEN'
                ORDER BY tr.revision_no DESC LIMIT 1""", (episode_id,)
            ).fetchone()
            timeline_count = int(connection.execute("SELECT COUNT(*) FROM timeline_revisions WHERE episode_id=?", (episode_id,)).fetchone()[0])
            subtitle = connection.execute(
                """SELECT sr.id, sr.revision_no, sr.format, sr.status, sr.created_at, sr.input_snapshot_json,
                (SELECT COUNT(*) FROM subtitle_cues sc WHERE sc.subtitle_revision_id=sr.id) AS cue_count
                FROM subtitle_revisions sr WHERE sr.episode_id=? ORDER BY sr.revision_no DESC LIMIT 1""", (episode_id,)
            ).fetchone()
            subtitle_count = int(connection.execute("SELECT COUNT(*) FROM subtitle_revisions WHERE episode_id=?", (episode_id,)).fetchone()[0])
            audio_rows = connection.execute(
                "SELECT source_license_status,license_evidence_json FROM audio_bindings WHERE episode_id=?", (episode_id,)
            ).fetchall()
            render = connection.execute(
                """SELECT erv.id, erv.timeline_revision_id, erv.revision, erv.integrity_status AS status, erv.duration_ms, erv.mime_type, erv.sha256,
                erv.input_snapshot_json, erv.ffmpeg_command_json, erv.execution_log_text, erv.created_at
                FROM episode_render_versions erv WHERE erv.episode_id=? ORDER BY erv.created_at DESC LIMIT 1""", (episode_id,)
            ).fetchone()
            render_count = int(connection.execute("SELECT COUNT(*) FROM episode_render_versions WHERE episode_id=?", (episode_id,)).fetchone()[0])
            render_verified_count = int(connection.execute("SELECT COUNT(*) FROM episode_render_versions WHERE episode_id=? AND integrity_status='VERIFIED'", (episode_id,)).fetchone()[0])
            delivery = connection.execute(
                """SELECT dp.id, dp.episode_render_version_id, dp.status, dp.rel_path, dp.manifest_sha256, dp.created_at,
                erv.timeline_revision_id
                FROM delivery_packages dp JOIN episode_render_versions erv ON erv.id=dp.episode_render_version_id
                WHERE erv.episode_id=? ORDER BY dp.created_at DESC LIMIT 1""", (episode_id,)
            ).fetchone()
            delivery_count = int(connection.execute("SELECT COUNT(*) FROM delivery_packages dp JOIN episode_render_versions erv ON erv.id=dp.episode_render_version_id WHERE erv.episode_id=?", (episode_id,)).fetchone()[0])
            delivery_verified = int(connection.execute("SELECT COUNT(*) FROM delivery_packages dp JOIN episode_render_versions erv ON erv.id=dp.episode_render_version_id WHERE erv.episode_id=? AND dp.status='VERIFIED'", (episode_id,)).fetchone()[0])
        latest_subtitle = dict(subtitle) if subtitle else None
        if latest_subtitle is not None:
            snapshot = json.loads(str(latest_subtitle.pop("input_snapshot_json")))
            latest_subtitle["authority_status"] = (
                "VERIFIED_SCRIPT"
                if snapshot.get("schema_version") == "localdrama.subtitle-authority.v1"
                else "LEGACY_INCOMPLETE"
            )
            latest_subtitle["source_document_version_id"] = snapshot.get("source_document_version_id")
            latest_subtitle["asr_alignment_only"] = bool(snapshot.get("asr_alignment")) and snapshot.get("asr_text_authority") is False
        latest_render = dict(render) if render else None
        if latest_render is not None:
            input_snapshot = json.loads(str(latest_render.pop("input_snapshot_json") or "{}"))
            ffmpeg_command = json.loads(str(latest_render.pop("ffmpeg_command_json") or "{}"))
            execution_log_text = str(latest_render.pop("execution_log_text") or "")
            try:
                execution_log = json.loads(execution_log_text) if execution_log_text else {}
            except json.JSONDecodeError:
                execution_log = {"raw": execution_log_text}
            latest_render["input_snapshot"] = input_snapshot
            latest_render["ffmpeg_command"] = ffmpeg_command
            latest_render["execution_log"] = execution_log
            latest_render["evidence_status"] = (
                "VERIFIED"
                if input_snapshot.get("schema_version") == "localdrama.episode-render-input.v1"
                and ffmpeg_command.get("executor") == "builtin:ffmpeg"
                else "LEGACY_INCOMPLETE"
            )
        return {
            "episode": {"id": str(episode["id"]), "code": str(episode["code"]), "title": str(episode["title"]), "project_id": str(episode["project_id"])},
            "timeline": {"revision_count": timeline_count, "latest": dict(timeline) if timeline else None, "latest_frozen": dict(latest_frozen_timeline) if latest_frozen_timeline else None},
            "subtitles": {"revision_count": subtitle_count, "latest": latest_subtitle},
            "audio": {
                "binding_count": len(audio_rows),
                "verified_local_count": sum(
                    1
                    for row in audio_rows
                    if str(row["source_license_status"]) in {"VERIFIED_LOCAL", "USER_OWNED", "PUBLIC_DOMAIN"}
                    and json.loads(str(row["license_evidence_json"])).get("schema_version") == "localdrama.audio-license-evidence.v1"
                ),
            },
            "renders": {"count": render_count, "verified_count": render_verified_count, "latest": latest_render},
            "delivery": {"count": delivery_count, "verified_count": delivery_verified, "latest": dict(delivery) if delivery else None},
            "observed_at": _now(), "read_only": True, "runtime_contacted": False, "network_contacted": False, "mutated": False,
        }

"""Read-only G8 timeline and delivery status projection."""

from __future__ import annotations

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
            timeline_count = int(connection.execute("SELECT COUNT(*) FROM timeline_revisions WHERE episode_id=?", (episode_id,)).fetchone()[0])
            subtitle = connection.execute(
                """SELECT sr.id, sr.revision_no, sr.format, sr.status, sr.created_at,
                (SELECT COUNT(*) FROM subtitle_cues sc WHERE sc.subtitle_revision_id=sr.id) AS cue_count
                FROM subtitle_revisions sr WHERE sr.episode_id=? ORDER BY sr.revision_no DESC LIMIT 1""", (episode_id,)
            ).fetchone()
            subtitle_count = int(connection.execute("SELECT COUNT(*) FROM subtitle_revisions WHERE episode_id=?", (episode_id,)).fetchone()[0])
            audio = connection.execute(
                """SELECT COUNT(*) AS total, SUM(CASE WHEN source_license_status IN ('VERIFIED_LOCAL', 'USER_OWNED') THEN 1 ELSE 0 END) AS verified
                FROM audio_bindings WHERE episode_id=?""", (episode_id,)
            ).fetchone()
            render = connection.execute(
                """SELECT erv.id, erv.integrity_status AS status, erv.duration_ms, erv.mime_type, erv.sha256, erv.created_at
                FROM episode_render_versions erv WHERE erv.episode_id=? ORDER BY erv.created_at DESC LIMIT 1""", (episode_id,)
            ).fetchone()
            render_count = int(connection.execute("SELECT COUNT(*) FROM episode_render_versions WHERE episode_id=?", (episode_id,)).fetchone()[0])
            delivery = connection.execute(
                """SELECT dp.id, dp.status, dp.rel_path, dp.manifest_sha256, dp.created_at
                FROM delivery_packages dp JOIN episode_render_versions erv ON erv.id=dp.episode_render_version_id
                WHERE erv.episode_id=? ORDER BY dp.created_at DESC LIMIT 1""", (episode_id,)
            ).fetchone()
            delivery_count = int(connection.execute("SELECT COUNT(*) FROM delivery_packages dp JOIN episode_render_versions erv ON erv.id=dp.episode_render_version_id WHERE erv.episode_id=?", (episode_id,)).fetchone()[0])
            delivery_verified = int(connection.execute("SELECT COUNT(*) FROM delivery_packages dp JOIN episode_render_versions erv ON erv.id=dp.episode_render_version_id WHERE erv.episode_id=? AND dp.status='VERIFIED'", (episode_id,)).fetchone()[0])
        return {
            "episode": {"id": str(episode["id"]), "code": str(episode["code"]), "title": str(episode["title"]), "project_id": str(episode["project_id"])},
            "timeline": {"revision_count": timeline_count, "latest": dict(timeline) if timeline else None},
            "subtitles": {"revision_count": subtitle_count, "latest": dict(subtitle) if subtitle else None},
            "audio": {"binding_count": int(audio["total"] or 0), "verified_local_count": int(audio["verified"] or 0)},
            "renders": {"count": render_count, "verified_count": 1 if render and render["status"] == "VERIFIED" else 0, "latest": dict(render) if render else None},
            "delivery": {"count": delivery_count, "verified_count": delivery_verified, "latest": dict(delivery) if delivery else None},
            "observed_at": _now(), "read_only": True, "runtime_contacted": False, "network_contacted": False, "mutated": False,
        }

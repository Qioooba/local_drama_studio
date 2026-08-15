"""Read-only G8 formal-exit readiness derived from persisted local evidence.

This projection intentionally does not create a timeline, render, review or
delivery package.  It reports the exact evidence required by blueprint 09 so
the UI can explain what is still missing without silently manufacturing a
sample or substituting a machine PASS for an approval decision.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


class G8ReadinessService:
    """Inspect formal G8 evidence without contacting runtimes or mutating DB."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def inspect(self, project_id: str, episode_id: str | None = None) -> dict[str, Any]:
        with self.database.connect() as connection:
            project = connection.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
            if project is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            episode = connection.execute(
                """SELECT e.id, e.code, e.title FROM episodes e
                JOIN seasons s ON s.id=e.season_id WHERE s.project_id=? AND (? IS NULL OR e.id=?)
                ORDER BY e.display_order LIMIT 1""",
                (project_id, episode_id, episode_id),
            ).fetchone()
            if episode is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "项目没有匹配的集", {"project_id": project_id, "episode_id": episode_id})
            eid = str(episode["id"])
            timeline = connection.execute(
                """SELECT tr.id, tr.revision_no, tr.status,
                (SELECT COUNT(*) FROM timeline_items ti WHERE ti.timeline_revision_id=tr.id) AS item_count,
                (SELECT COUNT(DISTINCT ma.owner_id) FROM timeline_items ti
                   JOIN media_versions mv ON mv.id=ti.media_version_id
                   JOIN media_assets ma ON ma.id=mv.media_asset_id
                  WHERE ti.timeline_revision_id=tr.id AND ti.track_type='VIDEO' AND ma.owner_type='SHOT') AS shot_count
                FROM timeline_revisions tr WHERE tr.episode_id=? ORDER BY tr.revision_no DESC LIMIT 1""",
                (eid,),
            ).fetchone()
            timeline_id = str(timeline["id"]) if timeline else None
            timeline_shots = int(timeline["shot_count"] or 0) if timeline else 0
            video_items = int(
                connection.execute(
                    "SELECT COUNT(*) FROM timeline_items ti JOIN timeline_revisions tr ON tr.id=ti.timeline_revision_id WHERE tr.episode_id=? AND ti.track_type='VIDEO'",
                    (eid,),
                ).fetchone()[0]
            )
            subtitle_count = int(connection.execute("SELECT COUNT(*) FROM subtitle_revisions WHERE episode_id=?", (eid,)).fetchone()[0])
            audio_rows = connection.execute(
                "SELECT track_type, source_license_status, license_evidence_json FROM audio_bindings WHERE episode_id=?", (eid,)
            ).fetchall()
            audio_tracks = {str(row["track_type"]).upper() for row in audio_rows}
            declared_audio = sum(
                1
                for row in audio_rows
                if str(row["source_license_status"]).upper() in {"VERIFIED_LOCAL", "USER_OWNED", "PUBLIC_DOMAIN"}
                and json.loads(str(row["license_evidence_json"])).get("schema_version") == "localdrama.audio-license-evidence.v1"
            )
            render = connection.execute(
                """SELECT erv.id, erv.integrity_status, erv.timeline_revision_id
                   FROM episode_render_versions erv WHERE erv.episode_id=? ORDER BY erv.created_at DESC LIMIT 1""",
                (eid,),
            ).fetchone()
            render_id = str(render["id"]) if render else None
            render_approval = connection.execute(
                """SELECT id FROM review_decisions
                   WHERE subject_type='EPISODE_RENDER_VERSION' AND subject_id=?
                     AND decision='APPROVED' AND is_stale=0
                   ORDER BY created_at DESC LIMIT 1""",
                (render_id,),
            ).fetchone() if render_id else None
            delivery = connection.execute(
                """SELECT dp.id, dp.status FROM delivery_packages dp
                   JOIN episode_render_versions erv ON erv.id=dp.episode_render_version_id
                  WHERE erv.episode_id=? ORDER BY dp.created_at DESC LIMIT 1""",
                (eid,),
            ).fetchone()
            verified_delivery = bool(delivery and str(delivery["status"]) == "VERIFIED")

        checks = [
            {
                "code": "THREE_REAL_SHOTS",
                "passed": timeline_shots >= 3,
                "count": timeline_shots,
                "required": 3,
                "detail": "timeline 中真实 SHOT-owned VIDEO 至少 3 个" if timeline_shots < 3 else "已发现 3 个以上真实镜头",
            },
            {
                "code": "DIALOGUE_ENVIRONMENT_SFX_MUSIC",
                "passed": {"DIALOGUE", "ENVIRONMENT", "SFX", "MUSIC"}.issubset(audio_tracks),
                "count": len(audio_rows),
                "required_tracks": ["DIALOGUE", "ENVIRONMENT", "SFX", "MUSIC"],
                "observed_tracks": sorted(audio_tracks),
                "detail": f"四类用户选择的本地音轨必须真实绑定；{declared_audio} 条含用户授权记录，缺失记录仅提示风险",
            },
            {
                "code": "SUBTITLES",
                "passed": subtitle_count > 0,
                "count": subtitle_count,
                "detail": "至少一个真实字幕 revision",
            },
            {
                "code": "TIMELINE_INPUT_LOCKED",
                "passed": bool(timeline and timeline_id and video_items > 0),
                "count": video_items,
                "detail": "timeline revision 与输入媒体快照必须存在",
            },
            {
                "code": "APPROVED_EPISODE_RENDER",
                "passed": bool(render and str(render["integrity_status"]) == "VERIFIED" and render_approval),
                "count": 1 if render_approval else 0,
                "detail": "整集 render 必须通过机器完整性并存在真实批准决定",
            },
            {
                "code": "VERIFIED_DELIVERY",
                "passed": verified_delivery,
                "count": 1 if verified_delivery else 0,
                "detail": "delivery manifest/hash verify 必须为 VERIFIED",
            },
            {
                "code": "TAMPER_DETECTION",
                "passed": verified_delivery,
                "count": 1 if verified_delivery else 0,
                "detail": "正式验收需记录改 1 字节后 verify=CORRUPT；当前无真实 delivery 样本" if not verified_delivery else "由已验证 delivery 进入篡改回归",
            },
        ]
        first_blocker = next((str(check["code"]) for check in checks if not check["passed"]), None)
        return {
            "gate": "G8",
            "status": "PASS" if all(bool(check["passed"]) for check in checks) else "IN_PROGRESS",
            "project_id": project_id,
            "episode": {"id": str(episode["id"]), "code": str(episode["code"]), "title": str(episode["title"])},
            "checks": checks,
            "next_required_action": first_blocker,
            "evidence": {
                "timeline_revision_id": timeline_id,
                "render_id": render_id,
                "delivery_id": str(delivery["id"]) if delivery else None,
                "audio_tracks": sorted(audio_tracks),
                "subtitle_revision_count": subtitle_count,
            },
            "observed_at": _now(),
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }

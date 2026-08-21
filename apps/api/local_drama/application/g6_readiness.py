"""Read-only G6 exit-demo readiness derived from persisted production evidence."""

from __future__ import annotations

from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


class G6ReadinessService:
    """Explain the next honest G6 gate without creating approvals or selections."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def inspect(self, project_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            project = connection.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
            if project is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})

            approved_keyframes = connection.execute(
                """SELECT mv.id, ma.id AS media_asset_id, ma.owner_type, ma.owner_id, ma.purpose
                FROM media_assets ma JOIN media_versions mv ON mv.id=ma.approved_version_id
                WHERE ma.project_id=? AND ma.owner_type='SHOT' AND ma.purpose='KEYFRAME'
                AND ma.media_kind='IMAGE' AND mv.stage='KEYFRAME' AND mv.integrity_status='VERIFIED'
                ORDER BY mv.created_at, mv.id""",
                (project_id,),
            ).fetchall()
            published_i2v = connection.execute(
                """SELECT epv.id, epv.workflow_version_id FROM execution_profile_versions epv
                JOIN workflow_versions wv ON wv.id=epv.workflow_version_id
                WHERE epv.capability='VIDEO_I2V' AND epv.status='PUBLISHED' AND wv.status='PUBLISHED'
                ORDER BY epv.version_no DESC LIMIT 1"""
            ).fetchone()

            selected_keyframe_id = str(approved_keyframes[0]["id"]) if approved_keyframes else None
            proxies: list[Any] = []
            winner = None
            formal = None
            formal_machine_qc = None
            formal_review = None
            if selected_keyframe_id:
                proxies = connection.execute(
                    """SELECT DISTINCT mv.id, gv.id AS variant_id, mv.source_job_attempt_id
                    FROM variant_input_bindings vib
                    JOIN generation_variants gv ON gv.id=vib.variant_id
                    JOIN generation_intents gi ON gi.id=gv.intent_id
                    JOIN jobs j ON j.subject_type='GENERATION_VARIANT' AND j.subject_id=gv.id AND j.state='SUCCEEDED'
                    JOIN job_attempts ja ON ja.job_id=j.id AND ja.state='SUCCEEDED'
                    JOIN media_versions mv ON mv.source_job_attempt_id=ja.id AND mv.stage='PROXY' AND mv.integrity_status='VERIFIED'
                    WHERE gi.project_id=? AND vib.role='FIRST_FRAME' AND vib.media_version_id=?
                    ORDER BY mv.id""",
                    (project_id, selected_keyframe_id),
                ).fetchall()
                proxy_ids = [str(row["id"]) for row in proxies]
                if proxy_ids:
                    placeholders = ",".join("?" for _ in proxy_ids)
                    winner = connection.execute(
                        f"""SELECT s.media_version_id, gv.id AS variant_id FROM selections s
                        JOIN media_versions mv ON mv.id=s.media_version_id
                        JOIN job_attempts ja ON ja.id=mv.source_job_attempt_id
                        JOIN jobs j ON j.id=ja.job_id
                        JOIN generation_variants gv ON gv.id=j.subject_id
                        WHERE s.selection_type='PROXY_WINNER' AND s.media_version_id IN ({placeholders})
                        ORDER BY s.created_at DESC LIMIT 1""",
                        proxy_ids,
                    ).fetchone()
            if winner is not None:
                formal = connection.execute(
                    """SELECT mv.id, gv.id AS variant_id FROM generation_variants gv
                    JOIN generation_intents gi ON gi.id=gv.intent_id
                    JOIN jobs j ON j.subject_type='GENERATION_VARIANT' AND j.subject_id=gv.id AND j.state='SUCCEEDED'
                    JOIN job_attempts ja ON ja.job_id=j.id AND ja.state='SUCCEEDED'
                    JOIN media_versions mv ON mv.source_job_attempt_id=ja.id AND mv.stage='FORMAL' AND mv.integrity_status='VERIFIED'
                    WHERE gi.project_id=? AND gv.parent_variant_id=? ORDER BY mv.created_at DESC LIMIT 1""",
                    (project_id, str(winner["variant_id"])),
                ).fetchone()
            if formal is not None:
                formal_id = str(formal["id"])
                formal_machine_qc = connection.execute(
                    """SELECT status FROM machine_check_runs WHERE subject_type='MEDIA_VERSION' AND subject_id=?
                    ORDER BY created_at DESC LIMIT 1""",
                    (formal_id,),
                ).fetchone()
                formal_review = connection.execute(
                    """SELECT decision, is_stale FROM review_decisions
                    WHERE subject_type='MEDIA_VERSION' AND subject_id=? ORDER BY created_at DESC LIMIT 1""",
                    (formal_id,),
                ).fetchone()

        checks = [
            {"code": "APPROVED_KEYFRAME", "passed": bool(approved_keyframes), "count": len(approved_keyframes)},
            {"code": "PUBLISHED_I2V_PROFILE", "passed": published_i2v is not None},
            {"code": "FOUR_REAL_PROXY_TAKES", "passed": len(proxies) >= 4, "count": len(proxies)},
            {"code": "HUMAN_PROXY_WINNER", "passed": winner is not None},
            {"code": "FORMAL_VIDEO", "passed": formal is not None},
            {"code": "FORMAL_MACHINE_QC", "passed": bool(formal_machine_qc and formal_machine_qc["status"] == "PASS")},
            {
                "code": "FORMAL_HUMAN_APPROVAL",
                "passed": bool(formal_review and formal_review["decision"] == "APPROVED" and not formal_review["is_stale"]),
            },
        ]
        first_blocker = next((item["code"] for item in checks if not item["passed"]), None)
        return {
            "gate": "G6",
            "status": "PASS" if first_blocker is None else "IN_PROGRESS",
            "project_id": project_id,
            "checks": checks,
            "next_required_action": first_blocker,
            "evidence": {
                "approved_keyframe_media_version_id": selected_keyframe_id,
                "i2v_profile_version_id": str(published_i2v["id"]) if published_i2v else None,
                "proxy_media_version_ids": [str(row["id"]) for row in proxies],
                "winner_media_version_id": str(winner["media_version_id"]) if winner else None,
                "formal_media_version_id": str(formal["id"]) if formal else None,
            },
            "mutated": False,
        }

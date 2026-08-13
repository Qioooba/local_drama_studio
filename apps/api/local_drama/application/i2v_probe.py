"""Read-only plan for the first real I2V evidence probe."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class I2VProbePlanService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def plan(self, project_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            if connection.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            keyframe = connection.execute(
                """SELECT mv.id AS media_version_id, mv.sha256, mv.byte_size, ma.owner_id AS shot_id,
                rd.id AS approval_id, rd.created_at AS approved_at
                FROM media_assets ma JOIN media_versions mv ON mv.id=ma.approved_version_id
                JOIN review_decisions rd ON rd.subject_type='MEDIA_VERSION' AND rd.subject_id=mv.id
                WHERE ma.project_id=? AND ma.owner_type='SHOT' AND ma.purpose='KEYFRAME'
                AND ma.media_kind='IMAGE' AND mv.stage='KEYFRAME' AND mv.integrity_status='VERIFIED'
                AND rd.decision='APPROVED' AND rd.is_stale=0
                ORDER BY rd.created_at DESC LIMIT 1""",
                (project_id,),
            ).fetchone()
            workflow_rows = connection.execute(
                """SELECT id, content_hash, revision, contract_json FROM workflow_versions
                WHERE status='PUBLISHED' ORDER BY published_at DESC, created_at DESC"""
            ).fetchall()
            selected_workflow: Any | None = next(
                (
                    row for row in workflow_rows
                    if json.loads(str(row["contract_json"] or "{}" )).get("capability") == "H3_FL2VA_I2V_CANDIDATE"
                ),
                None,
            )
            profile = connection.execute(
                """SELECT id, capability, status, manifest_sha256, revision FROM execution_profile_versions
                WHERE capability='I2V' ORDER BY CASE status WHEN 'PUBLISHED' THEN 0 ELSE 1 END, version_no DESC LIMIT 1"""
            ).fetchone()
        blockers: list[str] = []
        if keyframe is None:
            blockers.append("APPROVED_KEYFRAME_REQUIRED")
        if selected_workflow is None:
            blockers.append("PUBLISHED_FL2VA_WORKFLOW_REQUIRED")
        if profile is None:
            blockers.append("I2V_PROFILE_CANDIDATE_REQUIRED")
        snapshot = {
            "project_id": project_id,
            "purpose": "I2V_PROFILE_EVIDENCE_PROBE",
            "approved_keyframe": dict(keyframe) if keyframe else None,
            "workflow": {
                "id": str(selected_workflow["id"]), "content_hash": str(selected_workflow["content_hash"]), "revision": int(selected_workflow["revision"])
            } if selected_workflow else None,
            "candidate_profile": dict(profile) if profile else None,
            "semantic_inputs": {
                "PROMPT": "subtle natural breathing, gentle camera push-in, stable identity and lighting",
                "SEED": 260825,
                "DURATION_SECONDS": 4.0,
                "ASPECT_RATIO": "9:16",
                "OUTPUT_PREFIX": "local_drama/i2v_profile_probe",
            },
            "resource_policy": {"channel": "GPU_H3", "max_parallel": 1, "ephemeral_single_job": True},
        }
        return {
            "status": "READY" if not blockers else "BLOCKED",
            "blockers": blockers,
            "snapshot": snapshot,
            "plan_hash": hashlib.sha256(_canonical(snapshot).encode()).hexdigest(),
            "would_create_job": False,
            "would_contact_comfyui": False,
            "confirmation_required": True,
        }

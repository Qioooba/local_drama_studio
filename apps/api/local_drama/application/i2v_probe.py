"""Plan, submit and finalize one real local I2V Profile evidence probe."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.profiles import ProfileService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class I2VProbePlanService:
    def __init__(self, database: Database, settings: Settings | None = None) -> None:
        self.database = database
        self.settings = settings

    def plan(
        self,
        project_id: str,
        profile_version_id: str | None = None,
        workflow_version_id: str | None = None,
    ) -> dict[str, Any]:
        explicit_profile = bool(profile_version_id)
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
            profile = connection.execute(
                """SELECT * FROM execution_profile_versions
                WHERE id=? AND capability='VIDEO_I2V'""",
                (profile_version_id,),
            ).fetchone() if explicit_profile else connection.execute(
                """SELECT * FROM execution_profile_versions WHERE capability='VIDEO_I2V'
                ORDER BY CASE status WHEN 'PUBLISHED' THEN 0 ELSE 1 END,version_no DESC LIMIT 1"""
            ).fetchone()
            if workflow_version_id:
                selected_workflow = connection.execute(
                    """SELECT id, content_hash, revision, contract_json, node_bindings_json, status
                    FROM workflow_versions WHERE id=?""",
                    (workflow_version_id,),
                ).fetchone()
            elif explicit_profile and profile is not None and profile["workflow_version_id"]:
                selected_workflow = connection.execute(
                    """SELECT id, content_hash, revision, contract_json, node_bindings_json, status
                    FROM workflow_versions WHERE id=?""",
                    (profile["workflow_version_id"],),
                ).fetchone()
            else:
                selected_workflow = next(
                    (
                        row for row in connection.execute(
                            """SELECT id,content_hash,revision,contract_json,node_bindings_json,status
                            FROM workflow_versions WHERE status='PUBLISHED' ORDER BY published_at DESC,created_at DESC"""
                        ).fetchall()
                        if json.loads(str(row["contract_json"] or "{}")).get("capability") == "H3_FL2VA_I2V_CANDIDATE"
                    ),
                    None,
                )
            validation = connection.execute(
                """SELECT status,contract_hash FROM profile_validation_attestations
                WHERE profile_version_id=? ORDER BY created_at DESC LIMIT 1""",
                (profile_version_id,),
            ).fetchone() if profile is not None else None
            compatibility = connection.execute(
                """SELECT status,contract_hash FROM profile_compatibility_attestations
                WHERE profile_version_id=? ORDER BY created_at DESC LIMIT 1""",
                (profile_version_id,),
            ).fetchone() if profile is not None else None
        blockers: list[str] = []
        if keyframe is None:
            blockers.append("APPROVED_KEYFRAME_REQUIRED")
        workflow_contract = json.loads(str(selected_workflow["contract_json"] or "{}")) if selected_workflow else {}
        workflow_bindings = json.loads(str(selected_workflow["node_bindings_json"] or "{}")) if selected_workflow else {}
        if selected_workflow is None or selected_workflow["status"] != "PUBLISHED" or workflow_contract.get("capability") != "H3_FL2VA_I2V_CANDIDATE":
            blockers.append("PUBLISHED_FL2VA_WORKFLOW_REQUIRED")
        if profile is None:
            blockers.append("EXPLICIT_I2V_PROFILE_CANDIDATE_REQUIRED")
        elif explicit_profile and profile["status"] != "DRAFT":
            blockers.append("DRAFT_I2V_PROFILE_REQUIRED")
        elif explicit_profile:
            contract_payload = ProfileService._contract_payload(profile)
            contract_hash = ProfileService._contract_hash(contract_payload)
            if validation is None or validation["status"] != "PASS" or validation["contract_hash"] != contract_hash:
                blockers.append("PROFILE_VALIDATION_REQUIRED")
            if compatibility is None or compatibility["status"] != "PASS" or compatibility["contract_hash"] != contract_hash:
                blockers.append("PROFILE_COMPATIBILITY_REQUIRED")
        required_roles = {"PROMPT", "SEED", "FIRST_FRAME", "OUTPUT_PREFIX"}
        if explicit_profile and selected_workflow is not None and not required_roles.issubset(set(workflow_bindings)):
            blockers.append("WORKFLOW_SEMANTIC_BINDINGS_REQUIRED")
        semantic_inputs = {
            "PROMPT": "subtle natural breathing, gentle camera push-in, stable identity and lighting",
            "SEED": 260825,
            "DURATION_SECONDS": 4.458,
            "ASPECT_RATIO": "9:16",
            "SIGMA_POINTS": 20,
            "ACCELERATION": "off",
            "OUTPUT_PREFIX": "local_drama/i2v_profile_probe",
        }
        if selected_workflow is not None:
            semantic_inputs = {key: value for key, value in semantic_inputs.items() if key in workflow_bindings}
        snapshot = {
            "project_id": project_id,
            "purpose": "I2V_PROFILE_EVIDENCE_PROBE",
            "approved_keyframe": dict(keyframe) if keyframe else None,
            "workflow": {
                "id": str(selected_workflow["id"]), "content_hash": str(selected_workflow["content_hash"]), "revision": int(selected_workflow["revision"])
            } if selected_workflow else None,
            "workflow_selection": "EXPLICIT" if workflow_version_id else ("PROFILE_FROZEN" if explicit_profile and profile is not None and profile["workflow_version_id"] else "LATEST_PUBLISHED"),
            "candidate_profile": {
                "id": str(profile["id"]), "capability": str(profile["capability"]),
                "status": str(profile["status"]), "manifest_sha256": profile["manifest_sha256"],
                "revision": int(profile["revision"]),
                "execution_fingerprint": ProfileService._execution_fingerprint(profile),
            } if profile else None,
            "semantic_inputs": semantic_inputs,
            "resource_policy": {"channel": "GPU_H3", "max_parallel": 1, "ephemeral_single_job": True, "network_policy": "LOOPBACK_ONLY"},
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

    def submit(
        self,
        project_id: str,
        profile_version_id: str,
        workflow_version_id: str,
        plan_hash: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if self.settings is None:
            raise DomainRuleError("I2V_PROBE_SETTINGS_REQUIRED", "I2V 证据探测缺少本机运行设置")
        plan = self.plan(project_id, profile_version_id, workflow_version_id)
        if plan["status"] != "READY":
            raise DomainRuleError("I2V_PROBE_BLOCKED", "I2V 证据探测前置条件未满足", {"blockers": plan["blockers"]})
        if plan_hash != plan["plan_hash"]:
            raise DomainRuleError("I2V_PROBE_PLAN_STALE", "I2V 证据探测计划已变化，请重新预检")
        snapshot = plan["snapshot"]
        keyframe = snapshot["approved_keyframe"]
        workflow = snapshot["workflow"]
        candidate = snapshot["candidate_profile"]
        job_snapshot = {
            "purpose": "I2V_PROFILE_EVIDENCE_PROBE",
            "workflow_version_id": workflow["id"],
            "execution_snapshot": {
                "profile_version_id": candidate["id"],
                "workflow_version_id": workflow["id"],
                "workflow_content_hash": workflow["content_hash"],
                "profile_execution_fingerprint": candidate["execution_fingerprint"],
            },
            "semantic_inputs": snapshot["semantic_inputs"],
            "media_bindings": [{
                "role": "FIRST_FRAME", "media_version_id": keyframe["media_version_id"],
                "ordinal": 0, "weight": None, "source_approval_id": keyframe["approval_id"],
            }],
            "probe_plan_hash": plan_hash,
            "network_policy": "LOOPBACK_ONLY",
        }
        job = JobService(self.database, self.settings).create_job(
            project_id,
            "PROFILE_EVIDENCE_PROBE",
            "EXECUTION_PROFILE_VERSION",
            profile_version_id,
            "GPU_H3",
            job_snapshot,
            idempotency_key,
            execution_profile_version_id=profile_version_id,
            max_attempts=1,
        )
        return {"job": job, "plan": plan}

    def finalize(self, project_id: str, job_id: str) -> dict[str, Any]:
        if self.settings is None:
            raise DomainRuleError("I2V_PROBE_SETTINGS_REQUIRED", "I2V 证据探测缺少本机运行设置")
        job = JobService(self.database, self.settings).get_job(job_id)
        if (
            str(job["project_id"]) != project_id
            or str(job["type"]) != "PROFILE_EVIDENCE_PROBE"
            or str(job["subject_type"]) != "EXECUTION_PROFILE_VERSION"
        ):
            raise DomainRuleError("I2V_PROBE_JOB_INVALID", "Job 不是当前项目的 I2V Profile 证据探测")
        if str(job["state"]) != "SUCCEEDED":
            raise DomainRuleError("I2V_PROBE_JOB_NOT_SUCCEEDED", "I2V Profile 证据 Job 尚未成功", {"state": job["state"]})
        with self.database.connect() as connection:
            artifact = connection.execute(
                """SELECT a.id FROM artifacts a JOIN job_attempts ja ON ja.id=a.job_attempt_id
                WHERE ja.job_id=? AND a.kind='COMFY_OUTPUT' AND a.status='VERIFIED'
                ORDER BY a.created_at DESC,a.id DESC LIMIT 1""",
                (job_id,),
            ).fetchone()
        if artifact is None:
            raise DomainRuleError("I2V_PROBE_ARTIFACT_REQUIRED", "成功 Job 没有 VERIFIED Comfy 视频产物")
        media = MediaService(self.database, self.settings).promote_job_artifact(
            str(artifact["id"]), purpose="PROFILE_EVIDENCE", media_kind="VIDEO", stage="PROXY",
        )
        snapshot = job["input_snapshot"]
        profile = ProfileService(self.database, self.settings.manifest_path).publish_from_evidence(
            str(job["subject_id"]), str(media["media_version_id"]), str(snapshot["workflow_version_id"]),
        )
        return {"job": job, "media": media, "profile_version": profile}

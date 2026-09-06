"""Plan, submit and finalize one real local T2I (IMAGE) Profile evidence probe."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.profiles import ProfileService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.image_input_roles import COMFY_IMAGE_INPUT_ROLES, IDENTITY_REFERENCE_ROLES
from local_drama.infrastructure.database.sqlite import Database

T2I_WORKFLOW_CAPABILITY = "SDXL_T2I_CANDIDATE"
REQUIRED_SEMANTIC_ROLES = {"PROMPT"}
REFERENCE_IMAGE_CAPABILITIES = {"IMAGE_MULTI_VIEW", "IMAGE_EDIT", "IMAGE_EXPRESSION"}

CAPABILITY_EVIDENCE_PROMPTS = {
    "IMAGE_MULTI_VIEW": (
        "Create a clean professional character turnaround sheet from the supplied character reference. "
        "Show exactly three separate full-body views of the same character: front view, left side profile, "
        "and back view, arranged left to right on one plain white background. Preserve the reference identity, "
        "face, age, hairstyle, clothing, materials, colors, body proportions, and distinctive accessories exactly. "
        "Each view must show the complete figure from head to feet at the same scale with neutral standing posture. "
        "No perspective view, no cropped body, no close-up, no action pose, no scenery, no text, no labels, "
        "no extra person, no duplicated limb, no fused figures, and no costume redesign."
    ),
    "IMAGE_EDIT": (
        "Edit the supplied image while preserving the subject identity, facial features, hairstyle, outfit, "
        "materials, colors, body proportions, composition, and all details not explicitly changed."
    ),
    "IMAGE_EXPRESSION": (
        "Create a consistent character expression reference from the supplied image while preserving identity, "
        "facial structure, age, hairstyle, outfit, colors, and proportions."
    ),
}


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class T2IProbePlanService:
    """Mirror of the I2V evidence probe for text-to-image candidate profiles."""

    def __init__(self, database: Database, settings: Settings | None = None) -> None:
        self.database = database
        self.settings = settings

    def plan(
        self,
        project_id: str,
        profile_version_id: str | None = None,
        workflow_version_id: str | None = None,
        source_media_version_id: str | None = None,
        reference_media_version_ids: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        explicit_profile = bool(profile_version_id)
        with self.database.connect() as connection:
            if connection.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            profile = connection.execute(
                "SELECT * FROM execution_profile_versions WHERE id=? AND capability LIKE 'IMAGE_%'",
                (profile_version_id,),
            ).fetchone() if explicit_profile else connection.execute(
                """SELECT * FROM execution_profile_versions WHERE capability LIKE 'IMAGE_%'
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
                        if json.loads(str(row["contract_json"] or "{}")).get("capability") == T2I_WORKFLOW_CAPABILITY
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
            source_reference = connection.execute(
                """SELECT mv.id AS media_version_id,mv.sha256,mv.byte_size,mv.integrity_status,
                mv.mime_type,ma.project_id,sa.id AS asset_id,sa.name AS asset_name,sa.description AS asset_description
                FROM media_versions mv
                JOIN media_assets ma ON ma.id=mv.media_asset_id
                LEFT JOIN story_assets sa ON sa.canonical_media_version_id=mv.id
                WHERE mv.id=?""",
                (source_media_version_id,),
            ).fetchone() if source_media_version_id else None
        blockers: list[str] = []
        workflow_contract = json.loads(str(selected_workflow["contract_json"] or "{}")) if selected_workflow else {}
        workflow_bindings = json.loads(str(selected_workflow["node_bindings_json"] or "{}")) if selected_workflow else {}
        frozen_workflow_id = str(profile["workflow_version_id"] or "") if profile is not None else ""
        profile_capability = str(profile["capability"] or "") if profile is not None else ""
        needs_reference_image = profile_capability in REFERENCE_IMAGE_CAPABILITIES
        image_roles = sorted(set(workflow_bindings) & COMFY_IMAGE_INPUT_ROLES)
        supplied = dict(reference_media_version_ids or {})
        if source_media_version_id:
            supplied.setdefault("FIRST_FRAME", source_media_version_id)
        if set(supplied) - set(image_roles):
            blockers.append("WORKFLOW_REFERENCE_ROLE_UNSUPPORTED")
        required_images = set(image_roles)
        if needs_reference_image and not required_images:
            required_images.add("FIRST_FRAME")
        reference_inputs = []
        with self.database.connect() as connection:
            for role in sorted(required_images):
                ref = connection.execute(
                    """SELECT mv.id,mv.sha256,mv.byte_size,mv.integrity_status,mv.mime_type,ma.project_id
                    FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id WHERE mv.id=?""",
                    (supplied.get(role),),
                ).fetchone()
                if ref is None or str(ref["project_id"]) != project_id or ref["integrity_status"] != "VERIFIED" or not str(ref["mime_type"]).startswith("image/"):
                    blockers.append(f"VERIFIED_PROJECT_REFERENCE_IMAGE_REQUIRED:{role}")
                    continue
                reference_inputs.append({"role": role, "media_version_id": str(ref["id"]),
                    "sha256": str(ref["sha256"]), "byte_size": int(ref["byte_size"])})
        generic_t2i_profile = profile_capability in {"IMAGE_CONCEPT", "IMAGE_CHARACTER", "IMAGE_SCENE"}
        workflow_is_compatible = bool(
            selected_workflow is not None
            and (
                workflow_contract.get("capability") == T2I_WORKFLOW_CAPABILITY
                or workflow_contract.get("capability") == profile_capability
                or (generic_t2i_profile and workflow_contract.get("capability") == "IMAGE_CONCEPT")
                or (frozen_workflow_id and str(selected_workflow["id"]) == frozen_workflow_id)
            )
        )
        if selected_workflow is None or selected_workflow["status"] != "PUBLISHED" or not workflow_is_compatible:
            blockers.append("PUBLISHED_T2I_WORKFLOW_REQUIRED")
        if profile is None:
            blockers.append("EXPLICIT_IMAGE_PROFILE_CANDIDATE_REQUIRED")
        elif explicit_profile and profile["status"] != "DRAFT":
            blockers.append("DRAFT_IMAGE_PROFILE_REQUIRED")
        elif explicit_profile:
            contract_payload = ProfileService._contract_payload(profile)
            contract_hash = ProfileService._contract_hash(contract_payload)
            if validation is None or validation["status"] != "PASS" or validation["contract_hash"] != contract_hash:
                blockers.append("PROFILE_VALIDATION_REQUIRED")
            if compatibility is None or compatibility["status"] != "PASS" or compatibility["contract_hash"] != contract_hash:
                blockers.append("PROFILE_COMPATIBILITY_REQUIRED")
        required_roles = REQUIRED_SEMANTIC_ROLES | required_images
        if explicit_profile and selected_workflow is not None and not required_roles.issubset(set(workflow_bindings)):
            blockers.append("WORKFLOW_SEMANTIC_BINDINGS_REQUIRED")
        if needs_reference_image and not reference_media_version_ids:
            if source_reference is None:
                blockers.append("VERIFIED_PROJECT_REFERENCE_IMAGE_REQUIRED")
            elif (
                str(source_reference["project_id"]) != project_id
                or str(source_reference["integrity_status"]) != "VERIFIED"
                or not str(source_reference["mime_type"] or "").startswith("image/")
            ):
                blockers.append("VERIFIED_PROJECT_REFERENCE_IMAGE_REQUIRED")
        semantic_inputs = {
            "PROMPT": CAPABILITY_EVIDENCE_PROMPTS.get(
                profile_capability,
                "cinematic vertical drama keyframe, moody practical lighting, high detail",
            ),
            "SEED": 260826,
            "NEGATIVE_PROMPT": "collage, multiple panels, distorted anatomy, extra limbs, text, watermark",
            "OUTPUT_PREFIX": "local_drama/t2i_profile_probe",
        }
        identity_count = len(set(image_roles) & set(IDENTITY_REFERENCE_ROLES))
        if identity_count:
            cast = "exactly one person, alone" if identity_count == 1 else f"exactly {identity_count} people"
            semantic_inputs["PROMPT"] = (
                f"Create one cinematic vertical frame showing {cast} in a modern office. "
                f"The entire image must contain exactly {identity_count} human figure(s), one per supplied reference image. "
                "Use only the referenced person or people. No other person, bystander, colleague, background figure, "
                "reflection of a person, or duplicate is allowed. Preserve each reference person's face, gender, "
                "hair and clothing. Do not merge identities. Relaxed standing posture, realistic anatomy, "
                "one continuous scene, no collage."
            )
            semantic_inputs["NEGATIVE_PROMPT"] += ", extra people, bystanders, crowd, duplicate person, merged identities"
        if selected_workflow is not None:
            semantic_inputs = {key: value for key, value in semantic_inputs.items() if key in workflow_bindings}
        snapshot = {
            "project_id": project_id,
            "purpose": "T2I_PROFILE_EVIDENCE_PROBE",
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
            "source_reference": {
                "media_version_id": str(source_reference["media_version_id"]),
                "asset_id": str(source_reference["asset_id"] or "") or None,
                "asset_name": str(source_reference["asset_name"] or "") or None,
                "sha256": str(source_reference["sha256"]),
                "byte_size": int(source_reference["byte_size"]),
            } if source_reference is not None else None,
            "semantic_inputs": semantic_inputs,
            "reference_inputs": reference_inputs,
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
        source_media_version_id: str | None = None,
        reference_media_version_ids: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if self.settings is None:
            raise DomainRuleError("T2I_PROBE_SETTINGS_REQUIRED", "T2I 证据探测缺少本机运行设置")
        plan = self.plan(project_id, profile_version_id, workflow_version_id, source_media_version_id, reference_media_version_ids)
        if plan["status"] != "READY":
            raise DomainRuleError("T2I_PROBE_BLOCKED", "T2I 证据探测前置条件未满足", {"blockers": plan["blockers"]})
        if plan_hash != plan["plan_hash"]:
            raise DomainRuleError("T2I_PROBE_PLAN_STALE", "T2I 证据探测计划已变化，请重新预检")
        snapshot = plan["snapshot"]
        workflow = snapshot["workflow"]
        candidate = snapshot["candidate_profile"]
        source_reference = snapshot.get("source_reference")
        job_snapshot = {
            "purpose": "T2I_PROFILE_EVIDENCE_PROBE",
            "workflow_version_id": workflow["id"],
            "execution_snapshot": {
                "profile_version_id": candidate["id"],
                "workflow_version_id": workflow["id"],
                "workflow_content_hash": workflow["content_hash"],
                "profile_execution_fingerprint": candidate["execution_fingerprint"],
            },
            "semantic_inputs": snapshot["semantic_inputs"],
            "media_bindings": [{
                "role": ref["role"],
                "media_version_id": ref["media_version_id"],
                "ordinal": 0,
                "weight": None,
            } for ref in snapshot["reference_inputs"]],
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
            raise DomainRuleError("T2I_PROBE_SETTINGS_REQUIRED", "T2I 证据探测缺少本机运行设置")
        job = JobService(self.database, self.settings).get_job(job_id)
        if (
            str(job["project_id"]) != project_id
            or str(job["type"]) != "PROFILE_EVIDENCE_PROBE"
            or str(job["subject_type"]) != "EXECUTION_PROFILE_VERSION"
        ):
            raise DomainRuleError("T2I_PROBE_JOB_INVALID", "Job 不是当前项目的 T2I Profile 证据探测")
        snapshot = job["input_snapshot"]
        if not isinstance(snapshot, dict) or str(snapshot.get("purpose")) != "T2I_PROFILE_EVIDENCE_PROBE":
            raise DomainRuleError("T2I_PROBE_JOB_INVALID", "Job 快照不是 T2I 证据探测目的")
        if str(job["state"]) != "SUCCEEDED":
            raise DomainRuleError("T2I_PROBE_JOB_NOT_SUCCEEDED", "T2I Profile 证据 Job 尚未成功", {"state": job["state"]})
        with self.database.connect() as connection:
            artifact = connection.execute(
                """SELECT a.id FROM artifacts a JOIN job_attempts ja ON ja.id=a.job_attempt_id
                WHERE ja.job_id=? AND a.kind='COMFY_OUTPUT' AND a.status='VERIFIED'
                ORDER BY a.created_at DESC,a.id DESC LIMIT 1""",
                (job_id,),
            ).fetchone()
        if artifact is None:
            raise DomainRuleError("T2I_PROBE_ARTIFACT_REQUIRED", "成功 Job 没有 VERIFIED Comfy 图片产物")
        media = MediaService(self.database, self.settings).promote_job_artifact(
            str(artifact["id"]), purpose="PROFILE_EVIDENCE", media_kind="IMAGE", stage="KEYFRAME",
        )
        profile = ProfileService(self.database, self.settings.manifest_path).publish_from_evidence(
            str(job["subject_id"]), str(media["media_version_id"]), str(snapshot["workflow_version_id"]),
        )
        return {"job": job, "media": media, "profile_version": profile}

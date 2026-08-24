"""Durable queue facades for CPU-heavy creator operations.

The timeline service remains the single execution authority.  These facades
only validate immutable inputs and persist a resumable Job before returning to
the browser, so closing a page never loses a long-running enhancement or
delivery build.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from local_drama.application.jobs import JobService
from local_drama.application.timeline import TimelineService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class BackgroundOperationService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.timeline = TimelineService(database, settings)
        self.jobs = JobService(database, settings)

    def submit_enhancement(
        self,
        input_media_version_id: str,
        recipe_id: str,
        plan_hash: str,
        parameters: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        normalized_parameters = parameters or {}
        plan = self.timeline.plan_enhancement(input_media_version_id, recipe_id, normalized_parameters)
        if not hmac.compare_digest(str(plan["plan_hash"]), plan_hash):
            raise DomainRuleError("ENHANCEMENT_PLAN_STALE", "增强计划已变化，请重新预检")
        source = self.timeline.media.get_version(input_media_version_id)
        project_id = str(source["project_id"])
        snapshot = {
            "schema_version": "localdrama.enhancement-job.v1",
            "input_media_version_id": input_media_version_id,
            "recipe_id": recipe_id,
            "parameters": normalized_parameters,
            "plan_hash": plan_hash,
            "input_sha256": plan["snapshot"]["input_sha256"],
            "recipe_hash": plan["snapshot"]["recipe_hash"],
            "local_only": True,
            "network_contacted": False,
        }
        command_key = idempotency_key or f"enhancement:{project_id}:{plan_hash}"
        job = self.jobs.create_job(
            project_id,
            "VIDEO_ENHANCEMENT",
            "MEDIA_VERSION",
            input_media_version_id,
            "CPU",
            snapshot,
            command_key,
            priority=20,
            max_attempts=2,
        )
        if job.get("idempotent_replay"):
            job = {**self.jobs.get_job(str(job["id"])), "idempotent_replay": True}
        return {"plan": plan, "job": job, "idempotent_replay": bool(job.get("idempotent_replay"))}

    def submit_segmented_compose(
        self,
        timeline_revision_id: str,
        segments: list[dict[str, Any]],
        *,
        force_rerender: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        plan = self.timeline.preflight_segmented_episode_render(timeline_revision_id, segments)
        if plan["existing_render"] is not None and not force_rerender:
            return {"preflight": plan, "job": None, "render": plan["existing_render"], "idempotent_replay": True}
        if force_rerender and not idempotency_key:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "显式重新渲染必须提供 Idempotency-Key，防止网络重试重复创建版本")
        fingerprint = str(plan["compose_fingerprint"])
        snapshot = {
            "schema_version": "localdrama.segmented-compose-job.v1",
            "timeline_revision_id": timeline_revision_id,
            "segments": plan["input_snapshot"]["segments"],
            "compose_fingerprint": fingerprint,
            "force_rerender": force_rerender,
            "local_only": True,
            "network_contacted": False,
        }
        command_key = idempotency_key if force_rerender else f"segmented-compose:{timeline_revision_id}:{fingerprint}"
        job = self.jobs.create_job(
            str(plan["project_id"]),
            "SEGMENTED_EPISODE_COMPOSE",
            "TIMELINE_REVISION",
            timeline_revision_id,
            "CPU",
            snapshot,
            str(command_key),
            priority=10,
            max_attempts=2,
        )
        if job.get("idempotent_replay"):
            job = {**self.jobs.get_job(str(job["id"])), "idempotent_replay": True}
        return {"preflight": plan, "job": job, "render": None, "idempotent_replay": bool(job.get("idempotent_replay"))}

    def delivery_plan(
        self,
        episode_render_version_id: str,
        target_version_id: str,
        brand_kit_id: str | None = None,
        watermark_profile_id: str | None = None,
        compliance_policy_id: str | None = None,
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            render = connection.execute(
                """SELECT erv.id, erv.sha256, erv.revision, erv.integrity_status, s.project_id
                FROM episode_render_versions erv JOIN episodes e ON e.id=erv.episode_id
                JOIN seasons s ON s.id=e.season_id WHERE erv.id=?""",
                (episode_render_version_id,),
            ).fetchone()
            target = connection.execute(
                """SELECT dtv.id, dtv.revision, dtv.status, dt.project_id, dt.transport
                FROM delivery_target_versions dtv JOIN delivery_targets dt ON dt.id=dtv.delivery_target_id
                WHERE dtv.id=?""",
                (target_version_id,),
            ).fetchone()
            approval = connection.execute(
                """SELECT id, decision, is_stale, subject_revision FROM review_decisions
                WHERE subject_type='EPISODE_RENDER_VERSION' AND subject_id=?
                ORDER BY created_at DESC, id DESC LIMIT 1""",
                (episode_render_version_id,),
            ).fetchone()
        if render is None:
            raise DomainRuleError("EPISODE_RENDER_NOT_FOUND", "整集渲染版本不存在")
        if target is None:
            raise DomainRuleError("DELIVERY_TARGET_VERSION_NOT_FOUND", "交付目标版本不存在")
        if str(render["project_id"]) != str(target["project_id"]):
            raise DomainRuleError("DELIVERY_PROJECT_MISMATCH", "交付目标必须属于同一项目")
        if str(render["integrity_status"]) != "VERIFIED":
            raise DomainRuleError("EPISODE_RENDER_INTEGRITY_FAILED", "整集渲染尚未通过完整性校验")
        if str(target["transport"]) != "LOCAL_FILESYSTEM":
            raise DomainRuleError("REMOTE_TRANSPORT_DISABLED", "LOCAL_ONLY 首版只允许本地文件交付")
        if str(target["status"]) != "ACTIVE":
            raise DomainRuleError("DELIVERY_TARGET_VERSION_INACTIVE", "只能使用当前 ACTIVE 的交付目标版本创建新候选")
        if approval is None or str(approval["decision"]) != "APPROVED" or int(approval["is_stale"] or 0) != 0:
            raise DomainRuleError("EPISODE_RENDER_APPROVAL_REQUIRED", "只有最新、未过期的整集批准版本才能创建交付候选")
        if int(approval["subject_revision"]) != int(render["revision"]):
            raise DomainRuleError("EPISODE_RENDER_APPROVAL_STALE", "整集批准基于旧 revision，不能创建交付候选")
        inputs = {
            "episode_render_version_id": episode_render_version_id,
            "render_sha256": str(render["sha256"]),
            "render_revision": int(render["revision"]),
            "target_version_id": target_version_id,
            "target_revision": int(target["revision"]),
            "approval_id": str(approval["id"]),
            "brand_kit_id": brand_kit_id,
            "watermark_profile_id": watermark_profile_id,
            "compliance_policy_id": compliance_policy_id,
        }
        return {"project_id": str(render["project_id"]), "fingerprint": _fingerprint(inputs), "inputs": inputs}

    def submit_delivery(
        self,
        episode_render_version_id: str,
        target_version_id: str,
        brand_kit_id: str | None = None,
        watermark_profile_id: str | None = None,
        compliance_policy_id: str | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        plan = self.delivery_plan(
            episode_render_version_id,
            target_version_id,
            brand_kit_id,
            watermark_profile_id,
            compliance_policy_id,
        )
        snapshot = {
            "schema_version": "localdrama.delivery-build-job.v1",
            **plan["inputs"],
            "delivery_fingerprint": plan["fingerprint"],
            "local_only": True,
            "network_contacted": False,
        }
        command_key = idempotency_key or f"delivery:{plan['project_id']}:{plan['fingerprint']}"
        job = self.jobs.create_job(
            plan["project_id"],
            "DELIVERY_BUILD",
            "EPISODE_RENDER_VERSION",
            episode_render_version_id,
            "CPU",
            snapshot,
            command_key,
            priority=30,
            max_attempts=2,
        )
        if job.get("idempotent_replay"):
            job = {**self.jobs.get_job(str(job["id"])), "idempotent_replay": True}
        return {"preflight": {"status": "READY", **plan}, "job": job, "idempotent_replay": bool(job.get("idempotent_replay"))}

    def result(self, job_id: str) -> dict[str, Any]:
        """Return a presentation-safe operation result once its Job succeeds."""
        job = self.jobs.get_job(job_id)
        response: dict[str, Any] = {"job": job, "result_type": None, "result": None}
        if str(job["state"]) != "SUCCEEDED":
            return response
        snapshot = job["input_snapshot"]
        if job["type"] == "VIDEO_ENHANCEMENT":
            with self.database.connect() as connection:
                row = connection.execute(
                    """SELECT id FROM enhancement_runs
                    WHERE input_media_version_id=? AND recipe_id=? AND plan_hash=? AND status='SUCCEEDED'
                    ORDER BY created_at DESC, id DESC LIMIT 1""",
                    (snapshot["input_media_version_id"], snapshot["recipe_id"], snapshot["plan_hash"]),
                ).fetchone()
            if row is not None:
                response.update(result_type="ENHANCEMENT", result=self.timeline.get_enhancement_run(str(row["id"])))
        elif job["type"] == "DELIVERY_BUILD":
            with self.database.connect() as connection:
                row = connection.execute(
                    """SELECT id FROM delivery_packages
                    WHERE episode_render_version_id=? AND target_version_id=?
                    AND brand_kit_id IS ? AND watermark_profile_id IS ? AND compliance_policy_id IS ?
                    ORDER BY created_at DESC, id DESC LIMIT 1""",
                    (
                        snapshot["episode_render_version_id"],
                        snapshot["target_version_id"],
                        snapshot.get("brand_kit_id"),
                        snapshot.get("watermark_profile_id"),
                        snapshot.get("compliance_policy_id"),
                    ),
                ).fetchone()
            if row is not None:
                response.update(result_type="DELIVERY", result=self.timeline.get_delivery_package(str(row["id"])))
        elif job["type"] == "EPISODE_COMPOSE":
            plan = self.timeline.preflight_episode_render(str(snapshot["timeline_revision_id"]))
            if plan.get("existing_render") is not None:
                response.update(result_type="EPISODE_RENDER", result=plan["existing_render"])
        elif job["type"] == "SEGMENTED_EPISODE_COMPOSE":
            plan = self.timeline.preflight_segmented_episode_render(
                str(snapshot["timeline_revision_id"]), list(snapshot["segments"]),
            )
            if plan.get("existing_render") is not None:
                response.update(result_type="EPISODE_RENDER", result=plan["existing_render"])
        return response

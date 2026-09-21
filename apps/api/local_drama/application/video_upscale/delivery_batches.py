from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Callable

from local_drama.application.background_operations import BackgroundOperationService
from local_drama.application.jobs import JobService
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class VideoUpscaleDeliveryBatchService:
    """Atomically groups already-adopted SR renders into normal DELIVERY_BUILD jobs."""

    def __init__(
        self,
        database: DatabaseUnitOfWork,
        settings: Settings,
        *,
        background_factory: Callable[[Any, Settings], BackgroundOperationService] = BackgroundOperationService,
        jobs_factory: Callable[[Any, Settings], JobService] = JobService,
    ) -> None:
        self.database = database
        self.settings = settings
        self.background = background_factory(database, settings)
        self.jobs = jobs_factory(database, settings)

    def plan(
        self,
        project_id: str,
        items: list[dict[str, str]],
        *,
        brand_kit_id: str | None,
        watermark_profile_id: str | None,
        compliance_policy_id: str | None,
    ) -> dict[str, Any]:
        planned: list[dict[str, Any]] = []
        with self.database.connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
            for ordinal, requested in enumerate(items, start=1):
                episode_id = str(requested["episode_id"])
                target_version_id = str(requested["target_version_id"])
                selection = connection.execute(
                    """SELECT selection.selected_render_id,selection.revision AS selection_revision,
                    season.project_id,render.render_kind
                    FROM episode_delivery_selections selection
                    JOIN episodes episode ON episode.id=selection.episode_id
                    JOIN seasons season ON season.id=episode.season_id
                    JOIN episode_render_versions render ON render.id=selection.selected_render_id
                    WHERE selection.episode_id=? AND selection.target_slot=?""",
                    (episode_id, target_version_id),
                ).fetchone()
                if selection is None or str(selection["project_id"]) != project_id:
                    raise DomainRuleError(
                        "DELIVERY_SELECTION_REQUIRED",
                        "批量打包前必须为每集显式采用当前交付目标的成片版本",
                        {"episode_id": episode_id, "target_version_id": target_version_id},
                    )
                if str(selection["render_kind"]) != "SUPER_RESOLUTION":
                    raise DomainRuleError(
                        "UPSCALE_DELIVERY_SELECTION_REQUIRED",
                        "整剧超分批量打包只接受已采用的 AI 超分成片",
                        {"episode_id": episode_id},
                    )
                selected_render_id = str(selection["selected_render_id"])
                source_item = connection.execute(
                    """SELECT item.id FROM video_upscale_batch_items item
                    JOIN video_upscale_runs run ON run.id=item.current_run_id
                    WHERE run.output_render_id=? AND run.purpose='FULL'
                    ORDER BY run.created_at DESC,item.created_at DESC LIMIT 1""",
                    (selected_render_id,),
                ).fetchone()
                if source_item is None:
                    raise DomainRuleError("UPSCALE_DELIVERY_LINEAGE_MISSING", "已采用超分版本缺少批次来源关系")
                planned.append(
                    {
                        "ordinal": ordinal,
                        "episode_id": episode_id,
                        "target_version_id": target_version_id,
                        "selected_render_id": selected_render_id,
                        "selection_revision": int(selection["selection_revision"]),
                        "source_batch_item_id": str(source_item["id"]),
                    }
                )
        for item in planned:
            delivery = self.background.delivery_plan(
                item["selected_render_id"],
                item["target_version_id"],
                brand_kit_id,
                watermark_profile_id,
                compliance_policy_id,
                allow_inactive_target=True,
            )
            item["delivery_fingerprint"] = delivery["fingerprint"]
            item["delivery_inputs"] = delivery["inputs"]
            item["status"] = "READY"
        snapshot = {
            "schema_version": "localdrama.video-upscale-delivery-batch-plan.v1",
            "project_id": project_id,
            "items": planned,
            "brand_kit_id": brand_kit_id,
            "watermark_profile_id": watermark_profile_id,
            "compliance_policy_id": compliance_policy_id,
        }
        return {**snapshot, "status": "READY", "plan_hash": _hash(snapshot)}

    def submit(
        self,
        project_id: str,
        items: list[dict[str, str]],
        *,
        brand_kit_id: str | None,
        watermark_profile_id: str | None,
        compliance_policy_id: str | None,
        plan_hash: str,
        title: str,
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if not idempotency_key or len(idempotency_key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "批量打包必须提供 Idempotency-Key")
        plan = self.plan(
            project_id,
            items,
            brand_kit_id=brand_kit_id,
            watermark_profile_id=watermark_profile_id,
            compliance_policy_id=compliance_policy_id,
        )
        if not hmac.compare_digest(str(plan["plan_hash"]), plan_hash):
            raise DomainRuleError("DELIVERY_BATCH_PLAN_STALE", "批量交付计划已变化，请重新检查")
        request_hash = _hash(
            {
                "project_id": project_id,
                "plan_hash": plan_hash,
                "title": title,
            }
        )
        batch_id = str(uuid.uuid4())
        now = _now()
        created_jobs: list[dict[str, Any]] = []
        reused_jobs: list[str] = []
        with self.database.transaction() as connection:
            existing = connection.execute(
                """SELECT id,request_hash FROM video_upscale_delivery_batches
                WHERE project_id=? AND idempotency_key=?""",
                (project_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if not hmac.compare_digest(str(existing["request_hash"]), request_hash):
                    raise DomainRuleError("IDEMPOTENCY_KEY_CONFLICT", "相同 Idempotency-Key 已用于不同的批量交付")
                return {"batch": self.get(str(existing["id"])), "idempotent_replay": True}
            connection.execute(
                """INSERT INTO video_upscale_delivery_batches
                (id,project_id,title,idempotency_key,request_hash,plan_hash,
                 created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,1,'video-upscale-delivery-batch.v1')""",
                (batch_id, project_id, title, idempotency_key, request_hash, plan_hash, now, now, actor),
            )
            for item in plan["items"]:
                link = connection.execute(
                    "SELECT * FROM video_upscale_delivery_links WHERE delivery_fingerprint=?",
                    (item["delivery_fingerprint"],),
                ).fetchone()
                if link is None:
                    snapshot = {
                        "schema_version": "localdrama.delivery-build-job.v1",
                        **item["delivery_inputs"],
                        "delivery_fingerprint": item["delivery_fingerprint"],
                        "local_only": True,
                        "network_contacted": False,
                    }
                    job = self.jobs.create_job_in_transaction(
                        connection,
                        project_id,
                        "DELIVERY_BUILD",
                        "EPISODE_RENDER_VERSION",
                        item["selected_render_id"],
                        "CPU",
                        snapshot,
                        f"delivery:{project_id}:{item['delivery_fingerprint']}",
                        priority=30,
                        max_attempts=2,
                        actor=actor,
                        subject_kind="DELIVERY_PACKAGE",
                        scope_kind="PROJECT",
                        scope_project_id=project_id,
                        scope_episode_id=item["episode_id"],
                        stage_code="VIDEO_UPSCALE_DELIVERY",
                    )
                    link_id = str(uuid.uuid4())
                    connection.execute(
                        """INSERT INTO video_upscale_delivery_links
                        (id,batch_item_id,selected_render_id,target_version_id,delivery_fingerprint,
                         job_id,package_id,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,NULL,?,?,?,1,'video-upscale-delivery-link.v1')""",
                        (
                            link_id,
                            item["source_batch_item_id"],
                            item["selected_render_id"],
                            item["target_version_id"],
                            item["delivery_fingerprint"],
                            job["id"],
                            now,
                            now,
                            actor,
                        ),
                    )
                    created_jobs.append(job)
                else:
                    link_id = str(link["id"])
                    reused_jobs.append(str(link["job_id"] or ""))
                connection.execute(
                    """INSERT INTO video_upscale_delivery_batch_items
                    (id,delivery_batch_id,episode_id,ordinal,delivery_link_id,selected_render_id,target_version_id,
                     created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,?,?,?,?,?,?,1,'video-upscale-delivery-batch-item.v1')""",
                    (
                        str(uuid.uuid4()),
                        batch_id,
                        item["episode_id"],
                        item["ordinal"],
                        link_id,
                        item["selected_render_id"],
                        item["target_version_id"],
                        now,
                        now,
                        actor,
                    ),
                )
        return {
            "batch": self.get(batch_id),
            "jobs": created_jobs,
            "reused_job_ids": [job_id for job_id in reused_jobs if job_id],
            "idempotent_replay": False,
        }

    def get(self, batch_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            batch = connection.execute(
                "SELECT * FROM video_upscale_delivery_batches WHERE id=?",
                (batch_id,),
            ).fetchone()
            if batch is None:
                raise DomainRuleError("DELIVERY_BUILD_BATCH_NOT_FOUND", "批量交付不存在")
            rows = connection.execute(
                """SELECT item.*,link.delivery_fingerprint,link.job_id,link.package_id,
                job.state AS job_state,job.progress_json,job.last_error_code,job.last_error_detail_redacted,
                COALESCE(link.package_id,package.id) AS resolved_package_id,package.status AS package_status,
                package.rel_path AS package_rel_path,package.manifest_sha256
                FROM video_upscale_delivery_batch_items item
                JOIN video_upscale_delivery_links link ON link.id=item.delivery_link_id
                LEFT JOIN jobs job ON job.id=link.job_id
                LEFT JOIN delivery_packages package ON package.id=(
                    SELECT candidate.id FROM delivery_packages candidate
                    WHERE candidate.episode_render_version_id=item.selected_render_id
                      AND candidate.target_version_id=item.target_version_id
                    ORDER BY candidate.created_at DESC,candidate.id DESC LIMIT 1
                )
                WHERE item.delivery_batch_id=? ORDER BY item.ordinal,item.id""",
                (batch_id,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        states: dict[str, int] = {}
        for row in rows:
            item = dict(row)
            item["package_id"] = item.pop("resolved_package_id", None)
            item["progress"] = json.loads(str(item.pop("progress_json") or "{}"))
            state = str(item.get("job_state") or ("SUCCEEDED" if item.get("package_id") else "UNLINKED"))
            states[state] = states.get(state, 0) + 1
            items.append(item)
        result = dict(batch)
        result["items"] = items
        result["aggregate"] = {"total": len(items), "states": states}
        return result

    def list(self, project_id: str, *, limit: int = 50, cursor: int = 0) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 100))
        bounded_cursor = max(0, int(cursor))
        with self.database.connect() as connection:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM video_upscale_delivery_batches WHERE project_id=?",
                    (project_id,),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """SELECT id FROM video_upscale_delivery_batches WHERE project_id=?
                ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?""",
                (project_id, bounded_limit, bounded_cursor),
            ).fetchall()
        values = [self.get(str(row["id"])) for row in rows]
        return {
            "items": values,
            "page": {
                "limit": bounded_limit,
                "cursor": bounded_cursor,
                "total": total,
                "next_cursor": bounded_cursor + len(values) if bounded_cursor + len(values) < total else None,
            },
        }

    def retry_failed(self, batch_id: str, *, expected_revision: int, actor: str = "local-user") -> dict[str, Any]:
        job_ids: list[str] = []
        now = _now()
        with self.database.transaction() as connection:
            batch = connection.execute(
                "SELECT revision FROM video_upscale_delivery_batches WHERE id=?",
                (batch_id,),
            ).fetchone()
            if batch is None:
                raise DomainRuleError("DELIVERY_BUILD_BATCH_NOT_FOUND", "批量交付不存在")
            if int(batch["revision"]) != expected_revision:
                raise DomainRuleError("REVISION_CONFLICT", "批量交付状态已变化，请刷新后重试")
            rows = connection.execute(
                """SELECT DISTINCT job.id FROM video_upscale_delivery_batch_items item
                JOIN video_upscale_delivery_links link ON link.id=item.delivery_link_id
                JOIN jobs job ON job.id=link.job_id
                WHERE item.delivery_batch_id=? AND job.state IN ('FAILED','NEEDS_ATTENTION','ORPHANED')""",
                (batch_id,),
            ).fetchall()
            job_ids = [str(row["id"]) for row in rows]
            connection.execute(
                "UPDATE video_upscale_delivery_batches SET revision=revision+1,updated_at=? WHERE id=?",
                (now, batch_id),
            )
        jobs = [self.jobs.retry(job_id, actor=actor) for job_id in job_ids]
        return {"batch": self.get(batch_id), "jobs": jobs, "retried": len(jobs)}

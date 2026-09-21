from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Callable

from local_drama.application.jobs import JobService
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.path_policy import controlled_path
from local_drama.model_platform.application.capability_resolution import CapabilityScopeContext
from local_drama.model_platform.application.execution_job_links import ExecutionJobLink, ExecutionJobLinkService
from local_drama.model_platform.application.execution_planning import ExecutionPlanningService, ExecutionPreviewRequest
from local_drama.model_platform.application.execution_snapshots import ExecutionSnapshotDraft, ExecutionSnapshotService
from local_drama.model_platform.application.production_execution_registry import production_execution_handlers


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class VideoUpscaleBatchService:
    def __init__(
        self,
        database: DatabaseUnitOfWork,
        settings: Settings,
        *,
        jobs_factory: Callable[[Any, Settings], JobService] = JobService,
        planning_factory: Callable[[Any], ExecutionPlanningService] = ExecutionPlanningService,
        snapshot_factory: Callable[[Any], ExecutionSnapshotService] = ExecutionSnapshotService,
        link_factory: Callable[[Any], ExecutionJobLinkService] = ExecutionJobLinkService,
    ) -> None:
        self.database = database
        self.settings = settings
        self.jobs = jobs_factory(database, settings)
        self.planning = planning_factory(database)
        self.snapshots = snapshot_factory(database)
        self.links = link_factory(database)
        self.handlers = production_execution_handlers()

    def create(
        self,
        project_id: str,
        *,
        plan_id: str,
        plan_hash: str,
        title: str,
        acknowledged_warning_ids: list[str],
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if not idempotency_key or len(idempotency_key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "创建超分批次必须提供 Idempotency-Key")
        with self.database.connect() as connection:
            plan = connection.execute(
                "SELECT * FROM video_upscale_plans WHERE id=? AND project_id=?",
                (plan_id, project_id),
            ).fetchone()
        if plan is None:
            raise DomainRuleError("UPSCALE_PLAN_NOT_FOUND", "视频超分预检计划不存在")
        if str(plan["status"]) != "READY":
            raise DomainRuleError("UPSCALE_PLAN_NOT_READY", "只有 READY 的超分预检计划可以提交", {"status": plan["status"]})
        if not plan["plan_hash"] or not hmac.compare_digest(str(plan["plan_hash"]), plan_hash):
            raise DomainRuleError("UPSCALE_PLAN_STALE", "超分预检计划已变化，请重新检查")
        if datetime.fromisoformat(str(plan["expires_at"])) <= datetime.now(UTC):
            raise DomainRuleError("UPSCALE_PLAN_EXPIRED", "超分预检计划已过期，请重新检查")
        selection_snapshot = json.loads(str(plan["selection_snapshot_json"]))
        plan_items = json.loads(str(plan["items_json"]))
        profile_id = selection_snapshot.get("execution_profile_version_id")
        if not isinstance(profile_id, str) or not profile_id:
            raise DomainRuleError("UPSCALE_PROFILE_REQUIRED", "超分计划缺少已发布执行 Profile")
        warning_ids = {
            f"{item['episode_id']}:{warning['code']}"
            for item in plan_items
            for warning in item.get("warnings", [])
        }
        missing_acknowledgements = sorted(warning_ids - set(acknowledged_warning_ids))
        if missing_acknowledgements:
            raise DomainRuleError(
                "UPSCALE_WARNINGS_NOT_ACKNOWLEDGED",
                "提交前必须确认所有预检提醒",
                {"warning_ids": missing_acknowledgements},
            )

        prepared: list[dict[str, Any]] = []
        for item in plan_items:
            if item.get("blockers"):
                raise DomainRuleError("UPSCALE_PLAN_BLOCKED", "预检计划包含阻塞项，不能提交")
            pipeline_options = dict(item.get("effective_pipeline_options") or selection_snapshot["pipeline_options"])
            model_options = dict(item.get("effective_model_options") or selection_snapshot["model_options"])
            run_overrides = {
                key: model_options[key]
                for key in ("tile_size", "tta", "load_threads", "proc_threads", "save_threads")
                if key in model_options
            }
            run_id = str(uuid.uuid4())
            request = ExecutionPreviewRequest(
                capability_code="UPSCALE_VIDEO",
                scope=CapabilityScopeContext(project_id=project_id, episode_id=str(item["episode_id"])),
                semantic_inputs={"video_upscale_purpose": "FULL"},
                run_overrides=run_overrides,
                execution_profile_version_id=profile_id,
            )
            preview = self.planning.preview(request)
            if not preview.executable or preview.execution_profile_version_id is None or preview.adapter_code is None:
                raise DomainRuleError(
                    "MP_EXECUTION_NOT_READY",
                    "视频超分执行 Profile 当前不可运行",
                    {"blockers": list(preview.blockers)},
                )
            handler = self.handlers.resolve("UPSCALE_VIDEO", preview.adapter_code)
            fingerprint = _hash(
                {
                    "project_id": project_id,
                    "episode_id": item["episode_id"],
                    "source": item["source"],
                    "profile_resolution_hash": preview.resolution_hash,
                    "pipeline_options": pipeline_options,
                    "model_options": model_options,
                    "geometry": item["geometry"],
                    "purpose": "FULL",
                }
            )
            prepared.append(
                {
                    "item": item,
                    "run_id": run_id,
                    "preview": preview,
                    "handler": handler,
                    "fingerprint": fingerprint,
                    "variant_nonce": "" if selection_snapshot["existing_result_policy"] == "REUSE_EQUIVALENT" else run_id,
                    "pipeline_options": pipeline_options,
                    "model_options": model_options,
                }
            )

        request_payload = {
            "project_id": project_id,
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "title": title,
            "acknowledged_warning_ids": sorted(set(acknowledged_warning_ids)),
        }
        request_hash = _hash(request_payload)
        batch_id = str(uuid.uuid4())
        now = _now()
        created_jobs: list[dict[str, Any]] = []
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT id,request_hash FROM video_upscale_batches WHERE project_id=? AND idempotency_key=?",
                (project_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if not hmac.compare_digest(str(existing["request_hash"]), request_hash):
                    raise DomainRuleError("IDEMPOTENCY_KEY_CONFLICT", "相同 Idempotency-Key 已用于不同的超分批次")
                return {"batch": self.get(str(existing["id"])), "idempotent_replay": True}
            connection.execute(
                """INSERT INTO video_upscale_batches
                (id,project_id,preset_version_id,plan_id,plan_hash,title,control_state,idempotency_key,
                 request_hash,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,'ACTIVE',?,?,?,?,?,1,'video-upscale-batch.v1')""",
                (
                    batch_id,
                    project_id,
                    selection_snapshot["preset_version_id"],
                    plan_id,
                    plan_hash,
                    title,
                    idempotency_key,
                    request_hash,
                    now,
                    now,
                    actor,
                ),
            )
            for ordinal, prepared_item in enumerate(prepared, start=1):
                item = prepared_item["item"]
                existing_run = connection.execute(
                    """SELECT * FROM video_upscale_runs
                    WHERE project_id=? AND fingerprint=? AND variant_nonce=?""",
                    (project_id, prepared_item["fingerprint"], prepared_item["variant_nonce"]),
                ).fetchone()
                item_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO video_upscale_batch_items
                    (id,batch_id,episode_id,ordinal,source_descriptor_json,effective_options_json,
                     item_fingerprint,current_run_id,participation_state,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,?,?,?,NULL,'ACTIVE',?,?,?,1,'video-upscale-item.v1')""",
                    (
                        item_id,
                        batch_id,
                        item["episode_id"],
                        ordinal,
                        _json(item["source"]),
                        _json({
                            "pipeline": prepared_item["pipeline_options"],
                            "model": prepared_item["model_options"],
                            "geometry": item["geometry"],
                            "disk_budget": item.get("disk_budget", {}),
                        }),
                        prepared_item["fingerprint"],
                        now,
                        now,
                        actor,
                    ),
                )
                if existing_run is not None:
                    connection.execute(
                        "UPDATE video_upscale_batch_items SET current_run_id=? WHERE id=?",
                        (existing_run["id"], item_id),
                    )
                    continue
                preview = prepared_item["preview"]
                snapshot = self.snapshots.create_in_transaction(
                    connection,
                    ExecutionSnapshotDraft(
                        capability_code="UPSCALE_VIDEO",
                        execution_profile_version_id=str(preview.execution_profile_version_id),
                        resolved_parameters=preview.resolved_parameters,
                        semantic_inputs={"upscale_run_id": prepared_item["run_id"]},
                        resolution={
                            "resolution_hash": preview.resolution_hash,
                            "resolution_reason": preview.resolution_reason,
                            "assignment_chain": list(preview.assignment_chain),
                        },
                        network_policy=preview.network_policy,
                    ),
                )
                handler = prepared_item["handler"]
                scheduler_snapshot: dict[str, str] = {
                    "execution_snapshot_id": snapshot.id,
                    "content_hash": snapshot.content_hash,
                }
                if handler.gpu_runtime:
                    scheduler_snapshot["scheduler_runtime"] = handler.gpu_runtime
                job = self.jobs.create_job_in_transaction(
                    connection,
                    project_id,
                    "MODEL_PLATFORM_EXECUTION",
                    "MODEL_PLATFORM_EXECUTION",
                    snapshot.id,
                    handler.worker_channel,
                    scheduler_snapshot,
                    f"video-upscale-run:{project_id}:{prepared_item['fingerprint']}:{prepared_item['variant_nonce']}",
                    priority=50,
                    max_attempts=int(prepared_item["pipeline_options"]["max_attempts"]),
                    actor=actor,
                    subject_kind="VIDEO_UPSCALE_RUN",
                    scope_kind="PROJECT",
                    scope_project_id=project_id,
                    scope_episode_id=str(item["episode_id"]),
                    stage_code="MODEL_PLATFORM_EXECUTION",
                )
                self.links.link_in_transaction(
                    connection,
                    ExecutionJobLink(str(job["id"]), snapshot.id, handler.code, handler.version),
                )
                source_render_id = str(item["source"].get("render_id") or item["source"]["root_compose_render_id"])
                connection.execute(
                    """INSERT INTO video_upscale_runs
                    (id,project_id,purpose,batch_item_id,job_id,execution_snapshot_id,fingerprint,variant_nonce,
                     source_render_id,root_render_id,output_render_id,sample_artifact_id,qc_run_id,
                     progress_summary_json,episode_id,source_descriptor_json,effective_options_json,
                     sample_start_ms,sample_duration_ms,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?, 'FULL',?,?,?,?,?,?,?,NULL,NULL,NULL,'{}',?,?,?,NULL,NULL,?,?,?,1,'video-upscale-run.v1')""",
                    (
                        prepared_item["run_id"],
                        project_id,
                        item_id,
                        job["id"],
                        snapshot.id,
                        prepared_item["fingerprint"],
                        prepared_item["variant_nonce"],
                        source_render_id,
                        str(item["source"]["root_compose_render_id"]),
                        str(item["episode_id"]),
                        _json(item["source"]),
                        _json({
                            "pipeline": prepared_item["pipeline_options"],
                            "model": prepared_item["model_options"],
                            "geometry": item["geometry"],
                            "disk_budget": item.get("disk_budget", {}),
                        }),
                        now,
                        now,
                        actor,
                    ),
                )
                connection.execute(
                    "UPDATE video_upscale_batch_items SET current_run_id=? WHERE id=?",
                    (prepared_item["run_id"], item_id),
                )
                created_jobs.append(job)
        return {"batch": self.get(batch_id), "jobs": created_jobs, "idempotent_replay": False}

    def create_preview(
        self,
        project_id: str,
        *,
        plan_id: str,
        plan_hash: str,
        episode_id: str,
        start_ms: int,
        duration_ms: int,
        acknowledged_warning_ids: list[str],
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if not idempotency_key or len(idempotency_key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "创建超分样片必须提供 Idempotency-Key")
        with self.database.connect() as connection:
            plan = connection.execute(
                "SELECT * FROM video_upscale_plans WHERE id=? AND project_id=?",
                (plan_id, project_id),
            ).fetchone()
        if plan is None:
            raise DomainRuleError("UPSCALE_PLAN_NOT_FOUND", "视频超分预检计划不存在")
        if str(plan["status"]) != "READY":
            raise DomainRuleError("UPSCALE_PLAN_NOT_READY", "只有 READY 的超分预检计划可以试跑")
        if not plan["plan_hash"] or not hmac.compare_digest(str(plan["plan_hash"]), plan_hash):
            raise DomainRuleError("UPSCALE_PLAN_STALE", "超分预检计划已变化，请重新检查")
        if datetime.fromisoformat(str(plan["expires_at"])) <= datetime.now(UTC):
            raise DomainRuleError("UPSCALE_PLAN_EXPIRED", "超分预检计划已过期，请重新检查")
        selection_snapshot = json.loads(str(plan["selection_snapshot_json"]))
        plan_items = json.loads(str(plan["items_json"]))
        item = next((candidate for candidate in plan_items if str(candidate.get("episode_id")) == episode_id), None)
        if item is None:
            raise DomainRuleError("UPSCALE_PREVIEW_EPISODE_NOT_IN_PLAN", "试跑分集不属于当前预检计划")
        if item.get("blockers"):
            raise DomainRuleError("UPSCALE_PLAN_BLOCKED", "当前分集存在预检阻塞，不能试跑")
        warning_ids = {f"{episode_id}:{warning['code']}" for warning in item.get("warnings", [])}
        missing_acknowledgements = sorted(warning_ids - set(acknowledged_warning_ids))
        if missing_acknowledgements:
            raise DomainRuleError(
                "UPSCALE_WARNINGS_NOT_ACKNOWLEDGED",
                "试跑前必须确认当前分集的所有预检提醒",
                {"warning_ids": missing_acknowledgements},
            )
        profile_id = selection_snapshot.get("execution_profile_version_id")
        if not isinstance(profile_id, str) or not profile_id:
            raise DomainRuleError("UPSCALE_PROFILE_REQUIRED", "超分计划缺少已发布执行 Profile")
        pipeline_options = dict(item.get("effective_pipeline_options") or selection_snapshot["pipeline_options"])
        model_options = dict(item.get("effective_model_options") or selection_snapshot["model_options"])
        run_overrides = {
            key: model_options[key]
            for key in ("tile_size", "tta", "load_threads", "proc_threads", "save_threads")
            if key in model_options
        }
        run_id = str(uuid.uuid4())
        preview = self.planning.preview(
            ExecutionPreviewRequest(
                capability_code="UPSCALE_VIDEO",
                scope=CapabilityScopeContext(project_id=project_id, episode_id=episode_id),
                semantic_inputs={
                    "video_upscale_purpose": "PREVIEW",
                    "sample_start_ms": int(start_ms),
                    "sample_duration_ms": int(duration_ms),
                },
                run_overrides=run_overrides,
                execution_profile_version_id=profile_id,
            )
        )
        if not preview.executable or preview.execution_profile_version_id is None or preview.adapter_code is None:
            raise DomainRuleError("MP_EXECUTION_NOT_READY", "视频超分执行 Profile 当前不可运行", {"blockers": list(preview.blockers)})
        handler = self.handlers.resolve("UPSCALE_VIDEO", preview.adapter_code)
        fingerprint = _hash(
            {
                "project_id": project_id,
                "episode_id": episode_id,
                "source": item["source"],
                "profile_resolution_hash": preview.resolution_hash,
                "pipeline_options": pipeline_options,
                "model_options": model_options,
                "geometry": item["geometry"],
                "purpose": "PREVIEW",
                "sample_start_ms": int(start_ms),
                "sample_duration_ms": int(duration_ms),
            }
        )
        variant_nonce = f"preview:{hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()}"
        now = _now()
        with self.database.transaction() as connection:
            existing = connection.execute(
                """SELECT id,fingerprint FROM video_upscale_runs
                WHERE project_id=? AND purpose='PREVIEW' AND variant_nonce=? ORDER BY created_at LIMIT 1""",
                (project_id, variant_nonce),
            ).fetchone()
            if existing is not None:
                if not hmac.compare_digest(str(existing["fingerprint"]), fingerprint):
                    raise DomainRuleError("IDEMPOTENCY_KEY_CONFLICT", "相同 Idempotency-Key 已用于不同的超分样片")
                return {"run": self.get_run(str(existing["id"])), "idempotent_replay": True}
            snapshot = self.snapshots.create_in_transaction(
                connection,
                ExecutionSnapshotDraft(
                    capability_code="UPSCALE_VIDEO",
                    execution_profile_version_id=str(preview.execution_profile_version_id),
                    resolved_parameters=preview.resolved_parameters,
                    semantic_inputs={"upscale_run_id": run_id},
                    resolution={
                        "resolution_hash": preview.resolution_hash,
                        "resolution_reason": preview.resolution_reason,
                        "assignment_chain": list(preview.assignment_chain),
                    },
                    network_policy=preview.network_policy,
                ),
            )
            scheduler_snapshot: dict[str, str] = {
                "execution_snapshot_id": snapshot.id,
                "content_hash": snapshot.content_hash,
            }
            if handler.gpu_runtime:
                scheduler_snapshot["scheduler_runtime"] = handler.gpu_runtime
            job = self.jobs.create_job_in_transaction(
                connection,
                project_id,
                "MODEL_PLATFORM_EXECUTION",
                "MODEL_PLATFORM_EXECUTION",
                snapshot.id,
                handler.worker_channel,
                scheduler_snapshot,
                f"video-upscale-preview:{project_id}:{variant_nonce}",
                priority=55,
                max_attempts=int(pipeline_options["max_attempts"]),
                actor=actor,
                subject_kind="VIDEO_UPSCALE_PREVIEW",
                scope_kind="PROJECT",
                scope_project_id=project_id,
                scope_episode_id=episode_id,
                stage_code="MODEL_PLATFORM_EXECUTION",
            )
            self.links.link_in_transaction(
                connection,
                ExecutionJobLink(str(job["id"]), snapshot.id, handler.code, handler.version),
            )
            source_render_id = str(item["source"].get("render_id") or item["source"]["root_compose_render_id"])
            effective_options = {
                "pipeline": pipeline_options,
                "model": model_options,
                "geometry": item["geometry"],
                "disk_budget": item.get("disk_budget", {}),
            }
            connection.execute(
                """INSERT INTO video_upscale_runs
                (id,project_id,purpose,batch_item_id,job_id,execution_snapshot_id,fingerprint,variant_nonce,
                 source_render_id,root_render_id,output_render_id,sample_artifact_id,qc_run_id,
                 progress_summary_json,episode_id,source_descriptor_json,effective_options_json,
                 sample_start_ms,sample_duration_ms,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,'PREVIEW',NULL,?,?,?,?,?,?,NULL,NULL,NULL,'{}',?,?,?,?,?,?,?, ?,1,'video-upscale-preview.v1')""",
                (
                    run_id,
                    project_id,
                    job["id"],
                    snapshot.id,
                    fingerprint,
                    variant_nonce,
                    source_render_id,
                    str(item["source"]["root_compose_render_id"]),
                    episode_id,
                    _json(item["source"]),
                    _json(effective_options),
                    int(start_ms),
                    int(duration_ms),
                    now,
                    now,
                    actor,
                ),
            )
        return {"run": self.get_run(run_id), "job": job, "idempotent_replay": False}

    def get(self, batch_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            batch = connection.execute("SELECT * FROM video_upscale_batches WHERE id=?", (batch_id,)).fetchone()
            if batch is None:
                raise DomainRuleError("UPSCALE_BATCH_NOT_FOUND", "视频超分批次不存在")
            rows = connection.execute(
                """SELECT item.*,run.job_id,run.execution_snapshot_id,run.output_render_id,run.purpose,
                job.state AS job_state,job.progress_json,job.last_error_code,job.last_error_detail_redacted
                FROM video_upscale_batch_items item
                LEFT JOIN video_upscale_runs run ON run.id=item.current_run_id
                LEFT JOIN jobs job ON job.id=run.job_id
                WHERE item.batch_id=? ORDER BY item.ordinal,item.id""",
                (batch_id,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for row in rows:
            item = dict(row)
            item["source_descriptor"] = json.loads(str(item.pop("source_descriptor_json")))
            item["effective_options"] = json.loads(str(item.pop("effective_options_json")))
            item["progress"] = json.loads(str(item.pop("progress_json") or "{}"))
            state = str(item.get("job_state") or "UNLINKED")
            counts[state] = counts.get(state, 0) + 1
            item["effective_state"] = (
                "CANCELLED_FOR_BATCH"
                if item["participation_state"] == "CANCELLED"
                else "PAUSED_FOR_BATCH"
                if item["participation_state"] == "PAUSED" and state not in {"SUCCEEDED", "FAILED", "CANCELLED"}
                else state
            )
            items.append(item)
        result = dict(batch)
        result["items"] = items
        result["aggregate"] = {"total": len(items), "states": counts}
        return result

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT run.*,job.state AS job_state,job.progress_json,job.last_error_code,
                job.last_error_detail_redacted,job.finished_at,
                COALESCE(run.output_rel_path,render.rel_path) AS resolved_output_rel_path,
                COALESCE(run.output_sha256,render.sha256) AS resolved_output_sha256,
                render.probe_json AS output_probe_json
                FROM video_upscale_runs run
                JOIN jobs job ON job.id=run.job_id
                LEFT JOIN episode_render_versions render ON render.id=run.output_render_id
                WHERE run.id=?""",
                (run_id,),
            ).fetchone()
            if row is None:
                raise DomainRuleError("UPSCALE_RUN_NOT_FOUND", "视频超分运行不存在")
            chunks = connection.execute(
                """SELECT ordinal,start_frame,end_frame_exclusive,state,output_rel,output_sha256,
                frame_count,actual_parameters_json,updated_at FROM video_upscale_chunks
                WHERE run_id=? ORDER BY ordinal""",
                (run_id,),
            ).fetchall()
            references = connection.execute(
                """SELECT item.id,item.batch_id,item.episode_id,item.participation_state,batch.title
                FROM video_upscale_batch_items item JOIN video_upscale_batches batch ON batch.id=item.batch_id
                WHERE item.current_run_id=? ORDER BY batch.created_at,item.ordinal""",
                (run_id,),
            ).fetchall()
        result = dict(row)
        result["output_rel_path"] = result.pop("resolved_output_rel_path", None)
        result["output_sha256"] = result.pop("resolved_output_sha256", None)
        result["progress"] = json.loads(str(result.pop("progress_json") or "{}"))
        result["progress_summary"] = json.loads(str(result.pop("progress_summary_json") or "{}"))
        output_probe_json = result.pop("output_probe_json", None)
        result["output_probe"] = json.loads(str(output_probe_json or "{}")) if output_probe_json else None
        result["chunks"] = [
            {
                **dict(chunk),
                "actual_parameters": json.loads(str(chunk["actual_parameters_json"] or "{}")),
            }
            for chunk in chunks
        ]
        for chunk in result["chunks"]:
            chunk.pop("actual_parameters_json", None)
        result["batch_references"] = [dict(reference) for reference in references]
        if result.get("purpose") == "PREVIEW" and result.get("output_rel_path"):
            result["content_url"] = f"/api/v1/video-upscale-previews/{run_id}/content"
            result["source_content_url"] = f"/api/v1/video-upscale-previews/{run_id}/source-content"
        return result

    def preview_content_path(self, run_id: str) -> tuple[Any, str]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT run.purpose,run.output_rel_path,run.output_sha256,job.state AS job_state,project.root_rel
                FROM video_upscale_runs run
                JOIN jobs job ON job.id=run.job_id
                JOIN projects project ON project.id=run.project_id
                WHERE run.id=?""",
                (run_id,),
            ).fetchone()
        if row is None or str(row["purpose"]) != "PREVIEW":
            raise DomainRuleError("UPSCALE_PREVIEW_NOT_FOUND", "超分样片不存在")
        if str(row["job_state"]) != "SUCCEEDED" or not row["output_rel_path"]:
            raise DomainRuleError("UPSCALE_PREVIEW_NOT_READY", "超分样片尚未生成完成")
        project_root = self.settings.resolve_project_root(str(row["root_rel"]))
        path = controlled_path(
            project_root,
            str(row["output_rel_path"]),
            must_exist=True,
            require_file=True,
            code="UPSCALE_PREVIEW_FILE_MISSING",
        )
        return path, str(row["output_sha256"] or "")

    def preview_source_content_path(self, run_id: str) -> tuple[Any, str]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT run.purpose,run.source_descriptor_json,project.root_rel
                FROM video_upscale_runs run JOIN projects project ON project.id=run.project_id
                WHERE run.id=?""",
                (run_id,),
            ).fetchone()
        if row is None or str(row["purpose"]) != "PREVIEW":
            raise DomainRuleError("UPSCALE_PREVIEW_NOT_FOUND", "超分样片不存在")
        source = json.loads(str(row["source_descriptor_json"] or "{}"))
        project_root = self.settings.resolve_project_root(str(row["root_rel"]))
        path = controlled_path(
            project_root,
            str(source.get("rel_path") or ""),
            must_exist=True,
            require_file=True,
            code="UPSCALE_SOURCE_FILE_MISSING",
        )
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        actual_hash = digest.hexdigest()
        if not hmac.compare_digest(actual_hash, str(source.get("source_sha256") or "")):
            raise DomainRuleError("UPSCALE_SOURCE_HASH_CHANGED", "样片源文件内容已变化")
        return path, actual_hash

    def control(
        self,
        batch_id: str,
        *,
        action: str,
        expected_revision: int,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        allowed = {"PAUSE_PENDING", "PAUSE_ALL", "RESUME", "RETRY_FAILED", "CANCEL_UNFINISHED"}
        if action not in allowed:
            raise DomainRuleError("UPSCALE_BATCH_CONTROL_INVALID", "不支持的超分批次控制动作")
        terminal = {"SUCCEEDED", "FAILED", "CANCELLED"}
        now = _now()
        job_actions: list[tuple[str, str]] = []
        effects: list[dict[str, Any]] = []
        with self.database.transaction() as connection:
            batch = connection.execute("SELECT * FROM video_upscale_batches WHERE id=?", (batch_id,)).fetchone()
            if batch is None:
                raise DomainRuleError("UPSCALE_BATCH_NOT_FOUND", "视频超分批次不存在")
            if int(batch["revision"]) != expected_revision:
                raise DomainRuleError(
                    "REVISION_CONFLICT",
                    "超分批次状态已变化，请刷新后重试",
                    {"expected_revision": expected_revision, "actual_revision": int(batch["revision"])},
                )
            rows = connection.execute(
                """SELECT item.id,item.current_run_id,item.participation_state,run.job_id,job.state AS job_state
                FROM video_upscale_batch_items item
                LEFT JOIN video_upscale_runs run ON run.id=item.current_run_id
                LEFT JOIN jobs job ON job.id=run.job_id WHERE item.batch_id=? ORDER BY item.ordinal""",
                (batch_id,),
            ).fetchall()
            for row in rows:
                job_id = str(row["job_id"] or "")
                job_state = str(row["job_state"] or "UNLINKED")
                current_participation = str(row["participation_state"])
                next_participation = current_participation
                requested_job_action: str | None = None
                if action == "PAUSE_PENDING" and job_state in {"QUEUED", "PAUSED"} and current_participation == "ACTIVE":
                    next_participation = "PAUSED"
                    requested_job_action = "PAUSE"
                elif action == "PAUSE_ALL" and job_state not in terminal and current_participation == "ACTIVE":
                    next_participation = "PAUSED"
                    requested_job_action = "PAUSE"
                elif action == "RESUME" and current_participation == "PAUSED":
                    next_participation = "ACTIVE"
                    requested_job_action = "RESUME"
                elif action == "RETRY_FAILED" and job_state in {"FAILED", "NEEDS_ATTENTION", "ORPHANED"}:
                    next_participation = "ACTIVE"
                    requested_job_action = "RETRY"
                elif action == "CANCEL_UNFINISHED" and job_state not in terminal and current_participation != "CANCELLED":
                    next_participation = "CANCELLED"
                    requested_job_action = "CANCEL"
                if next_participation != current_participation:
                    connection.execute(
                        "UPDATE video_upscale_batch_items SET participation_state=?,updated_at=?,revision=revision+1 WHERE id=?",
                        (next_participation, now, row["id"]),
                    )
                shared_active = 0
                if row["current_run_id"]:
                    shared_active = int(
                        connection.execute(
                            """SELECT COUNT(*) FROM video_upscale_batch_items
                            WHERE current_run_id=? AND participation_state='ACTIVE'""",
                            (row["current_run_id"],),
                        ).fetchone()[0]
                    )
                should_control_job = bool(
                    requested_job_action
                    and job_id
                    and (
                        requested_job_action in {"RESUME", "RETRY"}
                        or shared_active == 0
                    )
                )
                if should_control_job:
                    job_actions.append((requested_job_action or "", job_id))
                if requested_job_action:
                    effects.append(
                        {
                            "item_id": str(row["id"]),
                            "run_id": str(row["current_run_id"] or "") or None,
                            "job_id": job_id or None,
                            "participation_state": next_participation,
                            "job_action": requested_job_action if should_control_job else "SHARED_RUN_CONTINUES",
                            "other_active_references": shared_active,
                        }
                    )
            control_state = {
                "PAUSE_PENDING": "PAUSED_PENDING",
                "PAUSE_ALL": "PAUSED",
                "RESUME": "ACTIVE",
                "RETRY_FAILED": "ACTIVE",
                "CANCEL_UNFINISHED": "CANCELLED",
            }[action]
            connection.execute(
                """UPDATE video_upscale_batches SET control_state=?,updated_at=?,revision=revision+1
                WHERE id=? AND revision=?""",
                (control_state, now, batch_id, expected_revision),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,before_revision,after_revision,summary,metadata_redacted_json)
                VALUES (?,'producer','VIDEO_UPSCALE_BATCH_CONTROL','video_upscale_batch',?,?,?,?,?)""",
                (
                    actor,
                    batch_id,
                    expected_revision,
                    expected_revision + 1,
                    f"视频超分批次控制 {action}",
                    _json({"action": action, "affected_item_count": len(effects)}),
                ),
            )
        job_results: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for job_action, job_id in job_actions:
            key = (job_action, job_id)
            if key in seen:
                continue
            seen.add(key)
            if job_action == "PAUSE":
                job_results.append(self.jobs.pause(job_id, actor=actor))
            elif job_action == "RESUME":
                job_results.append(self.jobs.resume(job_id, actor=actor))
            elif job_action == "RETRY":
                job_results.append(self.jobs.retry(job_id, actor=actor))
            elif job_action == "CANCEL":
                job_results.append(self.jobs.cancel(job_id, actor=actor))
        return {"batch": self.get(batch_id), "effects": effects, "jobs": job_results}

    def list(self, project_id: str, *, limit: int = 50, cursor: int = 0) -> dict[str, Any]:
        bounded_limit = max(1, min(limit, 100))
        bounded_cursor = max(0, cursor)
        with self.database.connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
            total = int(connection.execute("SELECT COUNT(*) FROM video_upscale_batches WHERE project_id=?", (project_id,)).fetchone()[0])
            rows = connection.execute(
                """SELECT id FROM video_upscale_batches WHERE project_id=?
                ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?""",
                (project_id, bounded_limit, bounded_cursor),
            ).fetchall()
        items = [self.get(str(row["id"])) for row in rows]
        return {
            "items": items,
            "page": {
                "limit": bounded_limit,
                "cursor": bounded_cursor,
                "total": total,
                "next_cursor": bounded_cursor + len(items) if bounded_cursor + len(items) < total else None,
            },
        }

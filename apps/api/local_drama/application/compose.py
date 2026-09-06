"""Durable Compose facade over the existing jobs/worker queue."""

from __future__ import annotations

import shutil
from typing import Any

from local_drama.application.jobs import JobService
from local_drama.application.timeline import TimelineService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


class ComposeService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.timeline = TimelineService(database, settings)
        self.jobs = JobService(database, settings)

    def preflight(self, timeline_revision_id: str, *, force_rerender: bool = False) -> dict[str, Any]:
        plan = self.timeline.preflight_episode_render(timeline_revision_id)
        items = plan["input_snapshot"].get("items", [])
        bindings = plan["input_snapshot"].get("audio_bindings", [])
        video_bytes = sum(int(item.get("byte_size") or 0) for item in items if isinstance(item, dict))
        audio_bytes = sum(int(item.get("media_byte_size") or 0) for item in bindings if isinstance(item, dict))
        # Conservative, explicit local policy: one final output plus concat
        # intermediate and encoder slack; mixed audio additionally creates PCM
        # and mux intermediates. It is evidence derived only from frozen input
        # byte sizes, never an invented precise output size.
        required_bytes = 0 if plan["existing_render"] is not None and not force_rerender else max(64 * 1024 * 1024, video_bytes * 3 + audio_bytes * 2)
        with self.database.connect() as connection:
            project = connection.execute("SELECT root_rel FROM projects WHERE id=?", (plan["project_id"],)).fetchone()
        if project is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": plan["project_id"]})
        project_root = self.settings.resolve_project_root(str(project["root_rel"]))
        expected_root = self.settings.projects_root.resolve()
        if not project_root.is_relative_to(expected_root) or not project_root.is_dir():
            raise DomainRuleError("PROJECT_ROOT_INVALID", "项目根目录不存在或越界", {"project_id": plan["project_id"]})
        try:
            free_bytes: int | None = int(shutil.disk_usage(project_root).free)
        except OSError:
            free_bytes = None
        blocking = free_bytes is None or free_bytes < required_bytes
        disk_gate = {
            "status": "BLOCKED" if blocking else "PASS", "blocking": blocking,
            "code": "COMPOSE_DISK_SPACE_LOW" if blocking else None,
            "free_bytes": free_bytes, "required_bytes": required_bytes,
            "estimate_source": "FROZEN_COMPOSE_INPUT_SIZE_POLICY_V1",
            "estimate_inputs": {"video_bytes": video_bytes, "audio_bytes": audio_bytes},
            "filesystem_source": "PROJECT_ROOT",
        }
        production_spec = plan.get("production_spec")
        production_blocked = isinstance(production_spec, dict) and str(production_spec.get("status")) != "READY"
        blockers = [*plan.get("blockers", []), *([disk_gate] if blocking else [])]
        return {
            **plan,
            "status": "BLOCKED" if (blockers or production_blocked) else "READY",
            "disk_gate": disk_gate,
            "blockers": blockers,
        }

    def submit(self, timeline_revision_id: str, *, force_rerender: bool = False, idempotency_key: str | None = None) -> dict[str, Any]:
        plan = self.preflight(timeline_revision_id, force_rerender=force_rerender)
        if plan["status"] != "READY":
            source_blockers = [item for item in plan.get("blockers", []) if str(item.get("code", "")).startswith(("TIMELINE_SOURCE_", "FFPROBE_"))]
            if source_blockers:
                raise DomainRuleError(
                    "COMPOSE_SOURCE_DURATION_BLOCKED", source_blockers[0]["message"],
                    {"timeline_revision_id": timeline_revision_id, "blockers": source_blockers},
                )
            production_spec = plan.get("production_spec")
            if isinstance(production_spec, dict) and str(production_spec.get("status")) != "READY":
                raise DomainRuleError(
                    "COMPOSE_PRODUCTION_SPEC_BLOCKED",
                    "整集合成被项目生产规格阻塞；请先修复交付画布或 VIDEO workflow 能力",
                    {"timeline_revision_id": timeline_revision_id, "production_spec": production_spec},
                )
            raise DomainRuleError(
                "COMPOSE_DISK_PREFLIGHT_BLOCKED", "Compose 输出及临时文件所需项目磁盘空间不足",
                {"timeline_revision_id": timeline_revision_id, "disk_gate": plan["disk_gate"]},
            )
        if plan["existing_render"] is not None and not force_rerender:
            return {"preflight": plan, "job": None, "render": plan["existing_render"], "idempotent_replay": True}
        fingerprint = str(plan["compose_fingerprint"])
        if force_rerender and not idempotency_key:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "显式重新渲染必须提供 Idempotency-Key，防止网络重试重复创建版本")
        command_key = idempotency_key if force_rerender else f"compose:{timeline_revision_id}:{fingerprint}"
        snapshot = {
            "schema_version": "localdrama.episode-compose-job.v1",
            "timeline_revision_id": timeline_revision_id,
            "compose_fingerprint": fingerprint,
            "renderer_contract": plan["input_snapshot"].get("renderer_contract"),
            "force_rerender": force_rerender,
            "force_command_id": idempotency_key if force_rerender else None,
            "local_only": True,
            "network_contacted": False,
        }
        job = self.jobs.create_job(
            str(plan["project_id"]), "EPISODE_COMPOSE", "TIMELINE_REVISION", timeline_revision_id,
            "CPU", snapshot, str(command_key), priority=10, max_attempts=2,
        )
        if job.get("idempotent_replay"):
            current_job = self.jobs.get_job(str(job["id"]))
            job = {**current_job, "idempotent_replay": True}
        return {"preflight": plan, "job": job, "render": None, "idempotent_replay": bool(job.get("idempotent_replay"))}

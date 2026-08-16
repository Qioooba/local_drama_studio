"""ComfyUI-backed generation lifecycle on top of the persistent local queue."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.workflows import WorkflowService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.database.sqlite import Database


class ComfyGenerationService:
    GPU_LEASE_SECONDS = 3600

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.jobs = JobService(database, settings)
        self.media = MediaService(database, settings)
        self.workflows = WorkflowService(database)
        self.comfy = ComfyClient(settings.comfy_base_url, settings.comfy_output_root)

    def submit_next(self, worker_id: str) -> dict[str, Any] | None:
        claim = self.jobs.claim(worker_id, ["GPU_H3"], lease_seconds=self.GPU_LEASE_SECONDS)
        if claim is None:
            return None
        job = claim["job"]
        attempt = claim["attempt"]
        token = str(attempt["lease_token"])
        snapshot = job["input_snapshot"]
        workflow_version_id = str(snapshot.get("workflow_version_id", ""))
        if not workflow_version_id:
            self.jobs.complete(
                str(attempt["id"]), token, worker_id, success=False, error_code="WORKFLOW_VERSION_REQUIRED", error_detail_redacted="missing workflow_version_id"
            )
            raise DomainRuleError("WORKFLOW_VERSION_REQUIRED", "Comfy Job 缺少 workflow_version_id")
        workflow_version = self.workflows.get_version(workflow_version_id)
        if workflow_version["status"] != "PUBLISHED":
            self.jobs.complete(
                str(attempt["id"]),
                token,
                worker_id,
                success=False,
                error_code="WORKFLOW_NOT_PUBLISHED",
                error_detail_redacted="workflow version is not published",
            )
            raise DomainRuleError("WORKFLOW_NOT_PUBLISHED", "Comfy Job 只能执行已通过本机验证并发布的 workflow")
        semantic_inputs = dict(snapshot.get("semantic_inputs", {}))
        # Only roles the published workflow actually declares are compiled into
        # the execution graph.  Metadata parameters frozen by the variant
        # (camera_plan, timed_directions, performance_bindings, motion_masks,
        # ...) stay in the immutable job snapshot for audit but must never be
        # written into a node input they do not belong to.
        declared_roles = set(workflow_version["node_bindings"])
        semantic_inputs = {role: value for role, value in semantic_inputs.items() if role in declared_roles}
        for binding in snapshot.get("media_bindings", []):
            if not isinstance(binding, dict) or not binding.get("role") or not binding.get("media_version_id"):
                raise DomainRuleError("COMFY_MEDIA_BINDING_INVALID", "Comfy Job 的媒体绑定快照无效")
            role = str(binding["role"])
            media_version_id = str(binding["media_version_id"])
            if role not in {"FIRST_FRAME", "END_FRAME", "MIDDLE_KEYFRAME", "REFERENCE_IMAGE"}:
                raise DomainRuleError("COMFY_MEDIA_ROLE_UNSUPPORTED", "Comfy 输入物化不支持该媒体角色", {"role": role})
            if self.settings.comfy_input_root is None:
                raise DomainRuleError("COMFY_INPUT_ROOT_REQUIRED", "媒体输入需要显式隔离的 Comfy input root")
            media, source = self.media.content_path(media_version_id)
            input_root = self.settings.comfy_input_root.resolve()
            input_root.mkdir(parents=True, exist_ok=True)
            suffix = Path(source).suffix.lower()
            target_name = f"{media_version_id}-{media['sha256'][:12]}{suffix}"
            target = (input_root / target_name).resolve()
            if not target.is_relative_to(input_root):
                raise DomainRuleError("COMFY_INPUT_PATH_INVALID", "Comfy 输入物化路径越界")
            if not target.exists():
                partial = target.with_name(f".partial-{target.name}")
                shutil.copyfile(source, partial)
                os.replace(partial, target)
            digest = hashlib.sha256()
            with target.open("rb") as copied:
                for chunk in iter(lambda: copied.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != str(media["sha256"]):
                target.unlink(missing_ok=True)
                raise DomainRuleError(
                    "COMFY_INPUT_INTEGRITY_MISMATCH",
                    "Comfy 输入物化后的 SHA-256 与已登记媒体不一致",
                    {"media_version_id": media_version_id},
                )
            semantic_inputs[role] = target_name
        compiled = self.workflows.compile_semantic_inputs(workflow_version_id, semantic_inputs)
        client_id = f"local-drama-{worker_id}"
        try:
            response = self.comfy.queue_prompt(
                compiled["workflow"], client_id=client_id, extra_data={"local_drama_job_id": job["id"], "compiled_hash": compiled["compiled_hash"]}
            )
        except DomainRuleError as error:
            if error.code == "COMFY_LOOPBACK_UNAVAILABLE":
                self.jobs.complete(
                    str(attempt["id"]),
                    token,
                    worker_id,
                    success=False,
                    error_code="COMFY_RUNTIME_UNAVAILABLE",
                    error_detail_redacted="Comfy loopback unavailable; attempt closed locally",
                )
            raise
        prompt_id = str(response["prompt_id"])
        self.jobs.attach_provider(
            str(attempt["id"]), token, worker_id, prompt_id, comfy_prompt_id=prompt_id, comfy_client_id=client_id, sandbox_rel_path=f"jobs/{job['id']}/comfy"
        )
        return {"job": job, "attempt": attempt, "prompt_id": prompt_id, "client_id": client_id, "compiled": compiled}

    def _active_attempt(self, attempt_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT a.*, j.project_id, j.state AS job_state FROM job_attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (attempt_id,)
            ).fetchone()
        if row is None:
            raise DomainRuleError("ATTEMPT_NOT_FOUND", "JobAttempt 不存在")
        if row["state"] not in {"CLAIMED", "RUNNING"} or not row["lease_token"]:
            raise DomainRuleError("ATTEMPT_NOT_ACTIVE", "Comfy Attempt 不再运行")
        return dict(row)

    def poll_attempt(self, attempt_id: str, worker_id: str) -> dict[str, Any]:
        attempt = self._active_attempt(attempt_id)
        prompt_id = str(attempt.get("comfy_prompt_id") or attempt.get("provider_job_id") or "")
        if not prompt_id:
            raise DomainRuleError("COMFY_PROMPT_ID_REQUIRED", "Attempt 尚未记录 Comfy prompt_id")
        try:
            history = self.comfy.history(prompt_id)
        except DomainRuleError as error:
            if error.code == "COMFY_LOOPBACK_UNAVAILABLE":
                result = self.jobs.complete(
                    str(attempt["id"]),
                    str(attempt["lease_token"]),
                    worker_id,
                    success=False,
                    error_code="COMFY_RUNTIME_UNAVAILABLE",
                    error_detail_redacted="Comfy loopback unavailable; attempt closed locally",
                    provider_job_id=prompt_id,
                )
                return {"status": "FAILED", "prompt_id": prompt_id, "result": result}
            raise
        item = history.get(prompt_id)
        if not item:
            try:
                queue = self.comfy.queue()
            except DomainRuleError as error:
                if error.code == "COMFY_LOOPBACK_UNAVAILABLE":
                    result = self.jobs.complete(
                        str(attempt["id"]),
                        str(attempt["lease_token"]),
                        worker_id,
                        success=False,
                        error_code="COMFY_RUNTIME_UNAVAILABLE",
                        error_detail_redacted="Comfy loopback unavailable; attempt closed locally",
                        provider_job_id=prompt_id,
                    )
                    return {"status": "FAILED", "prompt_id": prompt_id, "result": result}
                raise
            running_ids = self._queue_prompt_ids(queue.get("queue_running", []))
            pending_ids = self._queue_prompt_ids(queue.get("queue_pending", []))
            phase = "RUNNING" if prompt_id in running_ids else "QUEUED" if prompt_id in pending_ids else "PROVIDER_UNCONFIRMED"
            self.jobs.heartbeat(
                str(attempt["id"]),
                str(attempt["lease_token"]),
                worker_id,
                progress={"phase": phase, "prompt_id": prompt_id},
                lease_seconds=self.GPU_LEASE_SECONDS,
            )
            return {"status": phase, "prompt_id": prompt_id}
        status = item.get("status", {}).get("status_str")
        if status not in {"success", "error", "failure"}:
            self.jobs.heartbeat(
                str(attempt["id"]),
                str(attempt["lease_token"]),
                worker_id,
                progress={"phase": "RUNNING", "prompt_id": prompt_id},
                lease_seconds=self.GPU_LEASE_SECONDS,
            )
            return {"status": status or "RUNNING", "prompt_id": prompt_id}
        if status != "success":
            result = self.jobs.complete(
                str(attempt["id"]),
                str(attempt["lease_token"]),
                worker_id,
                success=False,
                error_code="COMFY_EXECUTION_FAILED",
                error_detail_redacted="Comfy history reported failure",
                provider_job_id=prompt_id,
            )
            return {"status": "FAILED", "prompt_id": prompt_id, "result": result}
        outputs = self.comfy.collect_outputs(item)
        output_root = self.settings.work_root / "jobs" / str(attempt["job_id"]) / "comfy"
        artifacts: list[dict[str, Any]] = []
        for source in outputs:
            target = output_root / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(f".partial-{target.name}")
            shutil.copyfile(source, partial)
            os.replace(partial, target)
            relative = target.relative_to(self.settings.work_root).as_posix()
            artifacts.append(self.jobs.register_artifact(str(attempt["id"]), "COMFY_OUTPUT", relative))
        result = self.jobs.complete(str(attempt["id"]), str(attempt["lease_token"]), worker_id, success=True, provider_job_id=prompt_id)
        return {"status": "SUCCEEDED", "prompt_id": prompt_id, "artifacts": artifacts, "result": result}

    @staticmethod
    def _queue_prompt_ids(entries: Any) -> set[str]:
        if not isinstance(entries, list):
            return set()
        return {
            str(entry[1])
            for entry in entries
            if isinstance(entry, (list, tuple)) and len(entry) > 1 and entry[1]
        }

    def interrupt_attempt(self, attempt_id: str, worker_id: str) -> dict[str, Any]:
        attempt = self._active_attempt(attempt_id)
        interrupted = self.comfy.interrupt()
        job = self.jobs.cancel(str(attempt["job_id"]))
        return {"attempt_id": attempt_id, "interrupt": interrupted, "job": job, "worker_id": worker_id}

    def recover_attempt(self, attempt_id: str, provider_job_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM job_attempts WHERE id=?", (attempt_id,)).fetchone()
        if row is None:
            raise DomainRuleError("ATTEMPT_NOT_FOUND", "JobAttempt 不存在")
        history = self.comfy.history(provider_job_id)
        item = history.get(provider_job_id)
        if not item or item.get("status", {}).get("status_str") != "success":
            raise DomainRuleError("COMFY_PROVIDER_NOT_SUCCEEDED", "Comfy history 尚未确认成功，不能恢复注册")
        outputs = self.comfy.collect_outputs(item)
        output_root = self.settings.work_root / "jobs" / str(row["job_id"]) / "comfy"
        artifacts: list[dict[str, Any]] = []
        for source in outputs:
            target = output_root / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(f".partial-{target.name}")
            shutil.copyfile(source, partial)
            os.replace(partial, target)
            artifacts.append(self.jobs.register_artifact(str(row["id"]), "COMFY_OUTPUT", target.relative_to(self.settings.work_root).as_posix()))
        result = self.jobs.recover_provider_success(attempt_id, provider_job_id)
        return {"status": "SUCCEEDED", "prompt_id": provider_job_id, "artifacts": artifacts, "result": result}

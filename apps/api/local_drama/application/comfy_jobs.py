"""ComfyUI-backed generation lifecycle on top of the persistent local queue."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
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

    def submit_next(
        self,
        worker_id: str,
        *,
        worker_session_id: str | None = None,
    ) -> dict[str, Any] | None:
        claim = self.jobs.claim(
            worker_id,
            ["GPU_H3"],
            lease_seconds=self.GPU_LEASE_SECONDS,
            worker_session_id=worker_session_id,
        )
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
        effective_snapshot = snapshot.get("execution_snapshot", {}).get("effective_configuration")
        if isinstance(effective_snapshot, dict):
            runtime_evidence = self._apply_effective_configuration(compiled["workflow"], effective_snapshot)
            if runtime_evidence["changed"]:
                compiled["compiled_hash"] = hashlib.sha256(
                    json.dumps(
                        {
                            "workflow_version_id": workflow_version_id,
                            "workflow": compiled["workflow"],
                            "semantic_inputs": semantic_inputs,
                            "effective_configuration_fingerprint": effective_snapshot.get("fingerprint"),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
            compiled["runtime_overrides"] = runtime_evidence
        client_id = f"local-drama-{worker_id}"
        try:
            response = self.comfy.queue_prompt(
                compiled["workflow"],
                client_id=client_id,
                extra_data={
                    "local_drama_job_id": job["id"],
                    "compiled_hash": compiled["compiled_hash"],
                    "runtime_overrides": compiled.get("runtime_overrides", {}),
                },
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
        self._persist_job_execution_evidence(str(job["id"]), compiled, prompt_id)
        self.jobs.attach_provider(
            str(attempt["id"]), token, worker_id, prompt_id, comfy_prompt_id=prompt_id, comfy_client_id=client_id, sandbox_rel_path=f"jobs/{job['id']}/comfy"
        )
        return {"job": job, "attempt": attempt, "prompt_id": prompt_id, "client_id": client_id, "compiled": compiled}

    def _persist_job_execution_evidence(self, job_id: str, compiled: dict[str, Any], prompt_id: str) -> None:
        """Persist the exact per-job graph evidence without storing secrets."""
        with self.database.transaction() as connection:
            row = connection.execute("SELECT input_snapshot_json FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise DomainRuleError("JOB_NOT_FOUND", "Job 不存在")
            snapshot = json.loads(str(row["input_snapshot_json"] or "{}"))
            execution_snapshot = snapshot.setdefault("execution_snapshot", {})
            execution_snapshot["compiled_workflow_sha256"] = compiled.get("compiled_hash")
            execution_snapshot["runtime_overrides_evidence"] = compiled.get("runtime_overrides", {})
            execution_snapshot["comfy_prompt_id"] = prompt_id
            connection.execute(
                "UPDATE jobs SET input_snapshot_json=?, updated_at=?, revision=revision+1 WHERE id=?",
                (json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")), datetime.now(UTC).isoformat(), job_id),
            )

    def _apply_effective_configuration(self, workflow: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        """Apply frozen runtime settings to the per-job Comfy graph.

        Profile workflow bytes remain immutable; this mutates only the copied
        graph returned by ``compile_semantic_inputs`` and returns evidence that
        is sent with the Comfy prompt.
        """
        settings = snapshot.get("effective_settings") if isinstance(snapshot.get("effective_settings"), dict) else {}
        sigma_points = settings.get("sigma_points")
        changed = False
        sigma_nodes: list[str] = []
        if isinstance(sigma_points, int) and not isinstance(sigma_points, bool):
            for node_id, node in workflow.items():
                if not isinstance(node, dict) or node.get("class_type") != "BasicScheduler":
                    continue
                inputs = node.get("inputs")
                if isinstance(inputs, dict) and "steps" in inputs:
                    inputs["steps"] = sigma_points
                    sigma_nodes.append(str(node_id))
                    changed = True

        acceleration = str(settings.get("acceleration") or "OFF").upper()
        lora_nodes = [
            str(node_id) for node_id, node in workflow.items()
            if isinstance(node, dict) and node.get("class_type") == "LoraLoaderModelOnly"
        ]
        lora_strength = settings.get("lora_strength", 1.0)
        lora_asset = None
        if acceleration == "TURBO_LORA":
            if not lora_nodes:
                from local_drama.application.h3_workflows import H3WorkflowFactory

                lora_asset = H3WorkflowFactory(self.settings).loader_assets()["turbo_lora_name"]
                numeric_ids = [int(node_id) for node_id in workflow if str(node_id).isdigit()]
                lora_id = str(max(numeric_ids, default=0) + 1)
                workflow[lora_id] = {
                    "class_type": "LoraLoaderModelOnly",
                    "inputs": {"model": ["1", 0], "lora_name": lora_asset, "strength_model": float(lora_strength)},
                }
                lora_nodes.append(lora_id)
                changed = True
            else:
                lora_asset = str(workflow[lora_nodes[0]].get("inputs", {}).get("lora_name") or "") or None
            model_ref = [lora_nodes[0], 0]
            for node in workflow.values():
                if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
                    continue
                if node.get("class_type") == "LoraLoaderModelOnly":
                    node["inputs"]["strength_model"] = float(lora_strength)
                elif node.get("class_type") in {"BasicScheduler", "BasicGuider"} and node["inputs"].get("model") == ["1", 0]:
                    node["inputs"]["model"] = model_ref
                    changed = True
        elif acceleration == "OFF" and lora_nodes:
            for lora_id in lora_nodes:
                lora_node = workflow.get(lora_id)
                base_ref = lora_node.get("inputs", {}).get("model", ["1", 0]) if isinstance(lora_node, dict) else ["1", 0]
                for node in workflow.values():
                    if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
                        continue
                    if node.get("class_type") in {"BasicScheduler", "BasicGuider"} and node["inputs"].get("model") == [lora_id, 0]:
                        node["inputs"]["model"] = base_ref
                        changed = True
                workflow.pop(lora_id, None)
                changed = True

        native_audio = settings.get("native_audio")
        audio_vae_nodes: list[str] = []
        audio_decode_nodes: list[str] = []
        if native_audio is False:
            for node_id, node in list(workflow.items()):
                if not isinstance(node, dict):
                    continue
                class_type = str(node.get("class_type") or "")
                inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
                if class_type == "VAELoader" and "audio" in str(inputs.get("vae_name") or "").lower():
                    audio_vae_nodes.append(str(node_id))
                if class_type == "VAEDecodeAudio":
                    audio_decode_nodes.append(str(node_id))
                if class_type == "MiniMaxH3ReferenceToVideo" and "audio_vae" in inputs:
                    raise DomainRuleError("H3_NATIVE_AUDIO_REQUIRED", "当前 Ref2V 节点要求 Audio VAE，不能关闭原生音频")
            for node_id in [*audio_vae_nodes, *audio_decode_nodes]:
                workflow.pop(node_id, None)
            removed_refs = set(audio_decode_nodes)
            for node in workflow.values():
                if isinstance(node, dict) and isinstance(node.get("inputs"), dict):
                    audio_ref = node["inputs"].get("audio")
                    if isinstance(audio_ref, list) and audio_ref and str(audio_ref[0]) in removed_refs:
                        node["inputs"].pop("audio", None)
            changed = changed or bool(audio_vae_nodes or audio_decode_nodes)

        return {
            "changed": changed,
            "sigma_points": sigma_points,
            "sigma_nodes": sigma_nodes,
            "acceleration": acceleration,
            "lora_nodes": [str(node_id) for node_id in lora_nodes if str(node_id) in workflow],
            "lora_asset": lora_asset,
            "lora_strength": float(lora_strength) if acceleration == "TURBO_LORA" else None,
            "native_audio": native_audio,
            "audio_vae_nodes_removed": audio_vae_nodes,
            "audio_decode_nodes_removed": audio_decode_nodes,
            "effective_configuration_fingerprint": snapshot.get("fingerprint"),
        }

    def run_once(
        self,
        worker_id: str,
        *,
        worker_session_id: str | None = None,
        poll_interval_seconds: float = 4.0,
        timeout_seconds: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> dict[str, Any] | None:
        """Claim and execute one durable GPU_H3 job through ComfyUI.

        This is the production bridge used by the generic WorkerSupervisor.  A
        browser-submitted GenerationVariant must therefore follow the same
        persistent JobAttempt, lease, provider-id and artifact lifecycle as the
        dedicated Comfy worker instead of being claimed by LocalMediaWorker and
        rejected as an unsupported job type.
        """
        submission = self.submit_next(worker_id, worker_session_id=worker_session_id)
        if submission is None:
            return None
        attempt = submission["attempt"]
        attempt_id = str(attempt["id"])
        timeout = float(timeout_seconds if timeout_seconds is not None else self.GPU_LEASE_SECONDS)
        deadline = time.monotonic() + max(1.0, timeout)
        while True:
            polled = self.poll_attempt(attempt_id, worker_id)
            status = str(polled.get("status", ""))
            if status in {"SUCCEEDED", "FAILED"}:
                return {**submission, "poll": polled, "result": polled.get("result")}
            if time.monotonic() >= deadline:
                active = self._active_attempt(attempt_id)
                result = self.jobs.complete(
                    attempt_id,
                    str(active["lease_token"]),
                    worker_id,
                    success=False,
                    error_code="COMFY_EXECUTION_TIMEOUT",
                    error_detail_redacted="Comfy execution exceeded the bounded GPU worker timeout",
                    provider_job_id=str(active.get("comfy_prompt_id") or active.get("provider_job_id") or "") or None,
                )
                return {
                    **submission,
                    "poll": {"status": "FAILED", "result": result},
                    "result": result,
                    "error": "COMFY_EXECUTION_TIMEOUT",
                }
            sleep(max(0.1, poll_interval_seconds))

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

    def recover_uncertain_successes(self, *, limit: int = 20) -> dict[str, Any]:
        """Recover provider-confirmed work without asking for attempt identifiers.

        Only attempts already marked ORPHANED/NEEDS_ATTENTION and carrying a
        durable provider id are inspected. The bounded scan is invoked by the
        background worker supervisor, never by a page read.
        """
        bounded_limit = max(1, min(int(limit), 100))
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT a.id,a.provider_job_id,a.job_id
                FROM job_attempts a JOIN jobs j ON j.id=a.job_id
                WHERE a.state IN ('ORPHANED','NEEDS_ATTENTION')
                  AND a.provider_job_id IS NOT NULL AND a.provider_job_id<>''
                  AND j.state IN ('ORPHANED','NEEDS_ATTENTION')
                ORDER BY a.updated_at LIMIT ?""",
                (bounded_limit,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            attempt_id = str(row["id"])
            provider_job_id = str(row["provider_job_id"])
            try:
                history = self.comfy.history(provider_job_id)
                provider_item = history.get(provider_job_id)
                provider_status = str((provider_item or {}).get("status", {}).get("status_str") or "UNKNOWN")
                if provider_status != "success":
                    items.append({"attempt_id": attempt_id, "job_id": str(row["job_id"]), "provider_job_id": provider_job_id, "status": provider_status})
                    continue
                recovered = self.recover_attempt(attempt_id, provider_job_id)
                items.append({"attempt_id": attempt_id, "job_id": str(row["job_id"]), "provider_job_id": provider_job_id, "status": "RECOVERED", "artifact_count": len(recovered["artifacts"])})
            except DomainRuleError as error:
                items.append({"attempt_id": attempt_id, "job_id": str(row["job_id"]), "provider_job_id": provider_job_id, "status": "DEFERRED", "error_code": error.code})
                if error.code == "COMFY_LOOPBACK_UNAVAILABLE":
                    break
        return {"inspected": len(rows), "recovered": sum(1 for item in items if item["status"] == "RECOVERED"), "items": items}

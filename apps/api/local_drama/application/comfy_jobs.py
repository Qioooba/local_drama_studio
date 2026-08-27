"""ComfyUI-backed generation lifecycle on top of the persistent local queue."""

from __future__ import annotations

import hashlib
import json
import shutil
import socket
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.workflows import WorkflowService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.filesystem.atomic import replace_path


class ComfyGenerationService:
    GPU_LEASE_SECONDS = 3600
    # A busy ComfyUI event loop (long VAE decodes block it for minutes) still
    # accepts TCP connections but cannot answer HTTP. Closing the attempt on
    # the first timeout killed healthy jobs in the field; only a conclusive
    # connection refusal, or a busy streak longer than any legitimate local
    # decode, may fail the attempt.
    BUSY_UNAVAILABLE_GRACE_SECONDS = 1800

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.jobs = JobService(database, settings)
        self.media = MediaService(database, settings)
        self.workflows = WorkflowService(database, settings)
        self.comfy = ComfyClient(
            settings.comfy_base_url,
            settings.comfy_output_root,
            allow_private_network=settings.allows_private_network,
        )

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
        runtime_binding = self._runtime_binding(workflow_version_id)
        semantic_inputs = dict(snapshot.get("semantic_inputs", {}))
        # Only roles the published workflow actually declares are compiled into
        # the execution graph.  Metadata parameters frozen by the variant
        # (camera_plan, timed_directions, performance_bindings, motion_masks,
        # ...) stay in the immutable job snapshot for audit but must never be
        # written into a node input they do not belong to.
        declared_roles = set(workflow_version["node_bindings"])
        snapshot_only_roles = sorted(str(role) for role in set(semantic_inputs) - declared_roles)
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
                replace_path(partial, target)
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
        compiled["effect_report"]["snapshot_only_roles"] = snapshot_only_roles
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
                    "workflow_runtime_binding": runtime_binding,
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
        self._record_provider_event(str(attempt["id"]), prompt_id, "QUEUED", {"client_id": client_id})
        return {
            "job": job,
            "attempt": attempt,
            "prompt_id": prompt_id,
            "client_id": client_id,
            "compiled": compiled,
            "websocket_capable": "number" in response or "node_errors" in response,
        }

    def _runtime_binding(self, workflow_version_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT b.contract_version_id,b.runtime_environment_version_id,
                c.content_hash AS contract_hash,r.environment_fingerprint
                FROM workflow_runtime_bindings b
                JOIN workflow_app_contract_versions c ON c.id=b.contract_version_id
                JOIN runtime_environment_versions r ON r.id=b.runtime_environment_version_id
                WHERE b.workflow_version_id=? AND c.status='PUBLISHED' AND r.status='PUBLISHED'""",
                (workflow_version_id,),
            ).fetchone()
        if row is None:
            # Existing published workflow versions remain runnable during the
            # explicit migration window, but execution evidence makes the
            # missing versioned binding visible instead of pretending it exists.
            return {"status": "LEGACY_UNBOUND", "workflow_version_id": workflow_version_id}
        return {"status": "BOUND", "workflow_version_id": workflow_version_id, **dict(row)}

    def _record_provider_event(
        self,
        attempt_id: str,
        prompt_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        progress: float | None = None,
        semantic_phase: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            last = connection.execute(
                "SELECT sequence_no,event_type,semantic_phase FROM provider_execution_events WHERE job_attempt_id=? ORDER BY sequence_no DESC LIMIT 1",
                (attempt_id,),
            ).fetchone()
            if event_type != "PROGRESS" and last is not None and str(last["event_type"]) == event_type and str(last["semantic_phase"] or "") == str(semantic_phase or ""):
                return
            sequence_no = int(last["sequence_no"]) + 1 if last else 1
            connection.execute(
                """INSERT INTO provider_execution_events
                (id,job_attempt_id,provider_prompt_id,sequence_no,event_type,semantic_phase,progress,payload_redacted_json,occurred_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), attempt_id, prompt_id, sequence_no, event_type, semantic_phase, progress, json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), now),
            )

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
            execution_snapshot["parameter_effect_report"] = compiled.get("effect_report", {})
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
        websocket = getattr(self.comfy, "websocket_events", None)
        runtime_reachable = False
        try:
            endpoint = urlparse(self.comfy.base_url)
            with socket.create_connection((str(endpoint.hostname), int(endpoint.port or 8188)), timeout=0.25):
                runtime_reachable = True
        except OSError:
            runtime_reachable = False
        if callable(websocket) and runtime_reachable and submission.get("websocket_capable") is True:
            try:
                websocket(
                    str(submission["prompt_id"]),
                    str(submission["client_id"]),
                    timeout_seconds=max(1.0, deadline - time.monotonic()),
                    on_event=lambda item: self._consume_provider_event(attempt_id, worker_id, item),
                )
            except DomainRuleError as error:
                # WebSocket is the preferred progress channel. History/queue
                # remains the recovery authority when the socket disconnects.
                self._record_provider_event(attempt_id, str(submission["prompt_id"]), "WEBSOCKET_DISCONNECTED", {"error_code": error.code})
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

    def _consume_provider_event(self, attempt_id: str, worker_id: str, item: dict[str, Any]) -> None:
        event_type = str(item.get("type") or "UNKNOWN").upper()
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        attempt = self._active_attempt(attempt_id)
        expected_prompt_id = str(attempt.get("comfy_prompt_id") or attempt.get("provider_job_id") or "")
        if data.get("prompt_id") and str(data.get("prompt_id")) != expected_prompt_id:
            return
        prompt_id = expected_prompt_id
        node_id = str(data.get("node") or "") or None
        semantic_phase = self._semantic_phase(str(attempt["job_id"]), node_id)
        progress = None
        value, maximum = data.get("value"), data.get("max")
        if isinstance(value, (int, float)) and isinstance(maximum, (int, float)) and maximum > 0:
            progress = max(0.0, min(1.0, float(value) / float(maximum)))
        redacted = {"node_id": node_id, "value": value if isinstance(value, (int, float)) else None, "max": maximum if isinstance(maximum, (int, float)) else None}
        self._record_provider_event(attempt_id, prompt_id, event_type, redacted, progress=progress, semantic_phase=semantic_phase)
        if event_type in {"EXECUTING", "PROGRESS", "EXECUTION_START", "EXECUTED"}:
            self.jobs.heartbeat(
                attempt_id,
                str(attempt["lease_token"]),
                worker_id,
                progress={"phase": semantic_phase or "RUNNING", "node": node_id, "percent": progress, "prompt_id": prompt_id},
                lease_seconds=self.GPU_LEASE_SECONDS,
            )

    def _semantic_phase(self, job_id: str, node_id: str | None) -> str | None:
        if not node_id:
            return None
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT c.semantic_phases_json FROM jobs j
                JOIN workflow_runtime_bindings b ON b.workflow_version_id=json_extract(j.input_snapshot_json,'$.workflow_version_id')
                JOIN workflow_app_contract_versions c ON c.id=b.contract_version_id
                WHERE j.id=?""",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        for phase in json.loads(str(row["semantic_phases_json"] or "[]")):
            if isinstance(phase, dict) and node_id in {str(item) for item in phase.get("node_ids", [])}:
                return str(phase.get("name") or "") or None
        return None

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

    def _fail_runtime_unavailable(
        self, attempt: dict[str, Any], worker_id: str, prompt_id: str
    ) -> dict[str, Any]:
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

    def _runtime_unavailable_outcome(
        self,
        attempt: dict[str, Any],
        worker_id: str,
        prompt_id: str,
        error: DomainRuleError,
    ) -> dict[str, Any]:
        """Decide between closing the attempt and riding out a busy runtime.

        Connection refused (nothing listening) is conclusive death. A request
        timeout means the ComfyUI event loop is blocked — for H3 video decodes
        this routinely lasts minutes while the prompt still finishes — so the
        attempt survives inside a bounded grace window tracked by durable
        PROVIDER_BUSY events. Errors without a root cause (legacy callers,
        tests) keep the historical close-immediately behavior.
        """
        cause = str((error.details or {}).get("cause") or "")
        if cause != "TimeoutError" and cause != "URLError":
            return self._fail_runtime_unavailable(attempt, worker_id, prompt_id)
        attempt_id = str(attempt["id"])
        # The grace clock measures *continuous* unresponsiveness: any provider
        # event recorded after the latest PROVIDER_BUSY entry proves the
        # runtime answered in between and restarts the window.
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT event_type,occurred_at FROM provider_execution_events
                WHERE job_attempt_id=? ORDER BY sequence_no DESC LIMIT 1""",
                (attempt_id,),
            ).fetchone()
        if row is not None and str(row["event_type"]) != "PROVIDER_BUSY":
            self._record_provider_event(
                attempt_id,
                prompt_id,
                "PROVIDER_BUSY",
                {"grace_seconds": self.BUSY_UNAVAILABLE_GRACE_SECONDS},
            )
        elif row is not None:
            busy_since = datetime.fromisoformat(str(row["occurred_at"]))
            if busy_since.tzinfo is None:
                busy_since = busy_since.replace(tzinfo=UTC)
            busy_seconds = (datetime.now(UTC) - busy_since).total_seconds()
            if busy_seconds > self.BUSY_UNAVAILABLE_GRACE_SECONDS:
                return self._fail_runtime_unavailable(attempt, worker_id, prompt_id)
        else:
            self._record_provider_event(
                attempt_id,
                prompt_id,
                "PROVIDER_BUSY",
                {"grace_seconds": self.BUSY_UNAVAILABLE_GRACE_SECONDS},
            )
        self.jobs.heartbeat(
            attempt_id,
            str(attempt["lease_token"]),
            worker_id,
            progress={"phase": "RUNNING", "prompt_id": prompt_id, "runtime_busy": True},
            lease_seconds=self.GPU_LEASE_SECONDS,
        )
        return {"status": "RUNNING", "prompt_id": prompt_id, "runtime_busy": True}

    def poll_attempt(self, attempt_id: str, worker_id: str) -> dict[str, Any]:
        attempt = self._active_attempt(attempt_id)
        prompt_id = str(attempt.get("comfy_prompt_id") or attempt.get("provider_job_id") or "")
        if not prompt_id:
            raise DomainRuleError("COMFY_PROMPT_ID_REQUIRED", "Attempt 尚未记录 Comfy prompt_id")
        try:
            history = self.comfy.history(prompt_id)
        except DomainRuleError as error:
            if error.code == "COMFY_LOOPBACK_UNAVAILABLE":
                return self._runtime_unavailable_outcome(attempt, worker_id, prompt_id, error)
            raise
        item = history.get(prompt_id)
        if not item:
            try:
                queue = self.comfy.queue()
            except DomainRuleError as error:
                if error.code == "COMFY_LOOPBACK_UNAVAILABLE":
                    return self._runtime_unavailable_outcome(attempt, worker_id, prompt_id, error)
                raise
            running_ids = self._queue_prompt_ids(queue.get("queue_running", []))
            pending_ids = self._queue_prompt_ids(queue.get("queue_pending", []))
            phase = "RUNNING" if prompt_id in running_ids else "QUEUED" if prompt_id in pending_ids else "PROVIDER_UNCONFIRMED"
            self._record_provider_event(attempt_id, prompt_id, phase, {"provider_visible": phase != "PROVIDER_UNCONFIRMED"})
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
            self._record_provider_event(attempt_id, prompt_id, "RUNNING", {"provider_status": str(status or "RUNNING")})
            self.jobs.heartbeat(
                str(attempt["id"]),
                str(attempt["lease_token"]),
                worker_id,
                progress={"phase": "RUNNING", "prompt_id": prompt_id},
                lease_seconds=self.GPU_LEASE_SECONDS,
            )
            return {"status": status or "RUNNING", "prompt_id": prompt_id}
        if status != "success":
            self._record_provider_event(attempt_id, prompt_id, "FAILED", {"provider_status": str(status)})
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
            replace_path(partial, target)
            relative = target.relative_to(self.settings.work_root).as_posix()
            artifacts.append(self.jobs.register_artifact(str(attempt["id"]), "COMFY_OUTPUT", relative))
        result = self.jobs.complete(str(attempt["id"]), str(attempt["lease_token"]), worker_id, success=True, provider_job_id=prompt_id)
        self._record_provider_event(attempt_id, prompt_id, "SUCCEEDED", {"artifact_count": len(artifacts)}, progress=1.0)
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
        prompt_id = str(attempt.get("comfy_prompt_id") or attempt.get("provider_job_id") or "")
        queue = self.comfy.queue()
        running_ids = self._queue_prompt_ids(queue.get("queue_running", []))
        pending_ids = self._queue_prompt_ids(queue.get("queue_pending", []))
        if prompt_id in pending_ids:
            interrupted = self.comfy.delete_pending(prompt_id)
            provider_action = "DELETE_PENDING"
        elif prompt_id in running_ids and running_ids == {prompt_id}:
            interrupted = self.comfy.interrupt()
            provider_action = "INTERRUPT_OWNED_RUNNING"
        elif prompt_id in running_ids:
            raise DomainRuleError(
                "COMFY_INTERRUPT_OWNERSHIP_UNSAFE",
                "ComfyUI 同时报告其他运行任务，拒绝执行全局 interrupt",
                {"target_prompt_id": prompt_id, "running_count": len(running_ids)},
            )
        else:
            interrupted = {"provider_visible": False}
            provider_action = "LOCAL_CANCEL_ONLY"
        job = self.jobs.cancel(str(attempt["job_id"]))
        self._record_provider_event(attempt_id, prompt_id, "CANCEL_REQUESTED", {"provider_action": provider_action})
        return {"attempt_id": attempt_id, "interrupt": interrupted, "provider_action": provider_action, "job": job, "worker_id": worker_id}

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
            replace_path(partial, target)
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

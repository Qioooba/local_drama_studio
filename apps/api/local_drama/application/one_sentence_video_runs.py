"""Durable plan/confirm/recover orchestration for one-sentence videos."""

from __future__ import annotations

import hashlib
import json
import math
import secrets
import socket
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import urlparse

from local_drama.application.generation import GenerationService
from local_drama.application.jobs import JobService
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.media import MediaService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.application.prompts import PromptService
from local_drama.application.reviews import ReviewService
from local_drama.application.workflows import WorkflowService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation import VariantPlan
from local_drama.domain.network_policy import endpoint_is_remote
from local_drama.domain.policies import VariantInput
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.database.shot_studio_command_repository import shot_studio_command_service
from local_drama.infrastructure.database.sqlite import Database

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}
ACTIVE_JOB_STATES = {"QUEUED", "CLAIMED", "RUNNING", "CANCEL_REQUESTED"}
RUN_MODES = {"DIRECT_T2V", "KEYFRAME_I2V"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class OneSentenceVideoRunService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        runtime_probe: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.profiles = ProfileService(database, settings.manifest_path)
        self.projects = ProjectService(database, settings.projects_root)
        self.shot_studio = shot_studio_command_service(database)
        self.generation = GenerationService(database, settings)
        self.prompts = PromptService(database)
        self.jobs = JobService(database, settings)
        self.media = MediaService(database, settings)
        self.reviews = ReviewService(database, settings)
        self.workflows = WorkflowService(database, settings)
        self.llm = LocalLLMService(database, settings)
        self._runtime_probe = runtime_probe or self._probe_runtime

    def _probe_runtime(self) -> dict[str, Any]:
        """Verify the local generation runtime exists before spending LLM calls.

        Planning only needs to know the runtime process is up — a submitted job
        legitimately queues behind whatever is already running. During long H3
        video decodes the ComfyUI event loop stops answering HTTP while the
        process stays alive, so a bare HTTP timeout must not fail the probe:
        fall back to a TCP liveness check and report the runtime as busy-ready.
        Only a refused connection means the runtime is genuinely down.
        """
        try:
            stats = ComfyClient(
                self.settings.comfy_base_url,
                self.settings.comfy_output_root,
                timeout_seconds=2.0,
                allow_private_network=self.settings.allows_private_network,
            ).system_stats()
        except DomainRuleError as error:
            if (error.details or {}).get("cause") not in {"TimeoutError", "URLError"}:
                raise DomainRuleError(
                    "ONE_SENTENCE_T2V_RUNTIME_UNAVAILABLE",
                    "本机视频生成 Runtime 当前不可用；尚未发送远端文本，也未创建项目",
                    {"cause": error.code, "project_created": False, "remote_llm_contacted": False},
                    suggested_action="启动生产 ComfyUI 与 worker 后重新规划",
                ) from error
            if not self._comfy_port_accepts():
                raise DomainRuleError(
                    "ONE_SENTENCE_T2V_RUNTIME_UNAVAILABLE",
                    "本机视频生成 Runtime 当前不可用；尚未发送远端文本，也未创建项目",
                    {"cause": error.code, "project_created": False, "remote_llm_contacted": False},
                    suggested_action="启动生产 ComfyUI 与 worker 后重新规划",
                ) from error
            return {"status": "READY", "endpoint": self.settings.comfy_base_url, "busy": True}
        return {"status": "READY", "endpoint": self.settings.comfy_base_url, "system_stats": stats}

    def _comfy_port_accepts(self) -> bool:
        endpoint = urlparse(self.settings.comfy_base_url)
        host, port = str(endpoint.hostname or "127.0.0.1"), int(endpoint.port or 8188)
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            return False

    def _ensure_derivative(
        self,
        media_version_id: str,
        kind: str,
        *,
        size: str = "small",
        frame: str = "poster",
    ) -> dict[str, Any]:
        """Idempotently submit a derivative and return its live job state.

        Job command replay intentionally preserves the original command response,
        so orchestration must resolve the returned identifier before making a
        readiness decision.
        """
        submitted = self.media.submit_derivative(
            media_version_id,
            kind,
            size=size,
            frame=frame,
        )
        return self.jobs.get_job(str(submitted["id"]))

    @staticmethod
    def _video_output_spec(workflow: dict[str, Any]) -> dict[str, Any]:
        graph = workflow.get("workflow") if isinstance(workflow.get("workflow"), dict) else {}
        width = height = frames = None
        fps: float | None = None
        for node in graph.values():
            if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
                continue
            inputs = node["inputs"]
            class_type = str(node.get("class_type") or "")
            if "Video" in class_type and all(key in inputs for key in ("width", "height", "length")):
                width, height, frames = int(inputs["width"]), int(inputs["height"]), int(inputs["length"])
            if class_type == "CreateVideo" and isinstance(inputs.get("fps"), (int, float)):
                fps = float(inputs["fps"])
        if not width or not height or not frames or not fps or fps <= 0:
            raise DomainRuleError(
                "ONE_SENTENCE_OUTPUT_SPEC_UNDECLARED",
                "所选 T2V Workflow 没有可验证的宽高、帧数或帧率，快捷入口不会伪造可调规格",
            )
        divisor = math.gcd(width, height)
        duration = frames / fps
        return {
            "width": width,
            "height": height,
            "frame_count": frames,
            "fps": int(fps) if fps.is_integer() else fps,
            "duration_seconds": round(duration, 3),
            "target_duration_ms": round(duration * 1000),
            "aspect_ratio": f"{width // divisor}:{height // divisor}",
            "source": "PUBLISHED_WORKFLOW",
            "editable": False,
        }

    @staticmethod
    def _image_output_spec(workflow: dict[str, Any]) -> dict[str, Any]:
        graph = workflow.get("workflow") if isinstance(workflow.get("workflow"), dict) else {}
        width = height = None
        for node in graph.values():
            if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
                continue
            inputs = node["inputs"]
            if str(node.get("class_type") or "") in {"EmptyLatentImage", "EmptySD3LatentImage"}:
                if isinstance(inputs.get("width"), int) and isinstance(inputs.get("height"), int):
                    width, height = int(inputs["width"]), int(inputs["height"])
                    break
        if not width or not height:
            raise DomainRuleError(
                "ONE_SENTENCE_IMAGE_SPEC_UNDECLARED",
                "所选文生图 Workflow 没有可验证的宽高，快捷入口不会猜测图片规格",
            )
        divisor = math.gcd(width, height)
        return {
            "width": width,
            "height": height,
            "aspect_ratio": f"{width // divisor}:{height // divisor}",
            "source": "PUBLISHED_WORKFLOW",
            "editable": False,
        }

    def _llm_authority(self, profile: dict[str, Any]) -> dict[str, Any]:
        execution = profile.get("execution") if isinstance(profile.get("execution"), dict) else {}
        connection = execution.get("provider_connection") if isinstance(execution.get("provider_connection"), dict) else None
        runtime = execution.get("runtime") if isinstance(execution.get("runtime"), dict) else {}
        capability = profile.get("capability_contract") if isinstance(profile.get("capability_contract"), dict) else {}
        bundle = profile.get("model_bundle") if isinstance(profile.get("model_bundle"), dict) else {}
        base_url = str((connection or {}).get("base_url") or runtime.get("base_url") or capability.get("base_url") or self.settings.llm_base_url)
        provider = str((connection or {}).get("protocol") or bundle.get("provider") or capability.get("provider") or "OLLAMA_LOOPBACK")
        model = str(bundle.get("model") or (connection or {}).get("model") or capability.get("model") or "")
        return {
            "provider_connection_id": execution.get("provider_connection_id"),
            "provider": "OLLAMA_LOOPBACK" if provider.upper() == "OLLAMA" else provider.upper(),
            "model": model,
            "base_url": base_url,
            "remote": endpoint_is_remote(base_url),
        }

    def _transition(self, run_id: str, state: str, stage: str, metadata: dict[str, Any] | None = None, **fields: Any) -> None:
        allowed = {
            "plan_json",
            "plan_hash",
            "execution_fingerprint",
            "project_id",
            "episode_id",
            "shot_id",
            "intent_id",
            "prompt_revision_id",
            "image_intent_id",
            "image_prompt_revision_id",
            "selected_candidate_id",
            "selected_image_media_version_id",
            "keyframe_review_id",
            "variant_id",
            "job_id",
            "media_version_id",
            "seed",
            "retry_count",
            "error_json",
            "confirmed_at",
            "completed_at",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise RuntimeError(f"unsupported run checkpoint fields: {sorted(unknown)}")
        now = _now()
        assignments = ["state=?", "stage=?", "updated_at=?", "revision=revision+1"]
        values: list[Any] = [state, stage, now]
        for key, value in fields.items():
            assignments.append(f"{key}=?")
            values.append(_json(value) if key.endswith("_json") and not isinstance(value, str) else value)
        values.append(run_id)
        with self.database.transaction() as connection:
            connection.execute(f"UPDATE one_sentence_video_runs SET {','.join(assignments)} WHERE id=?", values)
            connection.execute(
                "INSERT INTO one_sentence_video_run_events (run_id,stage,state,metadata_json,created_at) VALUES (?,?,?,?,?)",
                (run_id, stage, state, _json(metadata or {}), now),
            )

    def _fail(self, run_id: str, stage: str, error: Exception) -> None:
        payload = {
            "code": getattr(error, "code", type(error).__name__),
            "message": getattr(error, "message", str(error)),
            "details": getattr(error, "details", {}),
            "retryable": stage not in {"PLANNING"},
        }
        self._transition(run_id, "FAILED", stage, {"error_code": payload["code"]}, error_json=payload)

    def _ensure_not_cancelled(self, run_id: str) -> None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT state FROM one_sentence_video_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise DomainRuleError("ONE_SENTENCE_RUN_NOT_FOUND", "一句话生成 Run 不存在")
        if str(row["state"]) == "CANCELLED":
            raise DomainRuleError("ONE_SENTENCE_RUN_CANCELLED", "本次生成已取消")

    @staticmethod
    def _assert_idempotency_match(
        row: Any,
        *,
        story_sha256: str,
        language: str,
        llm_profile_version_id: str,
        image_profile_version_id: str | None,
        video_profile_version_id: str,
        mode: str,
        image_candidate_count: int,
        allow_remote_outbound: bool,
    ) -> None:
        expected = (
            story_sha256,
            language,
            llm_profile_version_id,
            str(image_profile_version_id or ""),
            video_profile_version_id,
            mode,
            int(image_candidate_count),
            int(allow_remote_outbound),
        )
        actual = (
            str(row["story_sha256"]),
            str(row["language"]),
            str(row["llm_profile_version_id"]),
            str(row["image_profile_version_id"] or ""),
            str(row["video_profile_version_id"]),
            str(row["mode"]),
            int(row["image_candidate_count"]),
            int(row["remote_outbound_confirmed"]),
        )
        if actual != expected:
            raise DomainRuleError(
                "IDEMPOTENCY_KEY_REUSED",
                "这个 Idempotency-Key 已绑定到另一份一句话生成请求，请为新规划使用新的 key",
                {"existing_run_id": str(row["id"])},
            )

    def plan(
        self,
        *,
        story: str,
        mode: str,
        language: str,
        llm_profile_version_id: str,
        image_profile_version_id: str | None,
        video_profile_version_id: str,
        image_candidate_count: int,
        allow_remote_outbound: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_story = story.strip()
        if len(normalized_story) < 2 or len(normalized_story) > 2000:
            raise DomainRuleError("VIDEO_STORY_REQUIRED", "视频描述必须是 2—2000 个字符")
        if language not in {"zh-CN", "en-US"}:
            raise DomainRuleError("VIDEO_PROMPT_LANGUAGE_INVALID", "快捷入口只支持简体中文或 English 提示词")
        if mode not in RUN_MODES:
            raise DomainRuleError("ONE_SENTENCE_MODE_INVALID", "生成模式必须是直接文生视频或先选首帧再图生视频")
        if not 1 <= image_candidate_count <= 8:
            raise DomainRuleError("ONE_SENTENCE_IMAGE_COUNT_INVALID", "候选图数量必须是 1—8")
        if mode == "KEYFRAME_I2V" and not image_profile_version_id:
            raise DomainRuleError("T2I_PROFILE_REQUIRED", "先出图模式必须选择已发布的文生图 Profile")
        if not idempotency_key or len(idempotency_key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "规划请求必须提供 Idempotency-Key")
        story_sha256 = hashlib.sha256(normalized_story.encode("utf-8")).hexdigest()
        with self.database.connect() as connection:
            existing = connection.execute(
                """SELECT id,story_sha256,language,llm_profile_version_id,image_profile_version_id,
                video_profile_version_id,mode,image_candidate_count,remote_outbound_confirmed
                FROM one_sentence_video_runs WHERE idempotency_key=?""",
                (idempotency_key,),
            ).fetchone()
        if existing is not None:
            self._assert_idempotency_match(
                existing,
                story_sha256=story_sha256,
                language=language,
                llm_profile_version_id=llm_profile_version_id,
                image_profile_version_id=image_profile_version_id,
                video_profile_version_id=video_profile_version_id,
                mode=mode,
                image_candidate_count=image_candidate_count,
                allow_remote_outbound=allow_remote_outbound,
            )
            return self.get(str(existing["id"]), reconcile=False)

        run_id = str(uuid.uuid4())
        now = _now()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """INSERT INTO one_sentence_video_runs
                    (id,idempotency_key,mode,state,stage,story_json,story_sha256,language,llm_profile_version_id,
                     image_profile_version_id,video_profile_version_id,image_candidate_count,
                     remote_outbound_confirmed,created_at,updated_at,created_by)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        run_id,
                        idempotency_key,
                        mode,
                        "PLANNING",
                        "RUNTIME_PREFLIGHT",
                        _json({"text": normalized_story}),
                        story_sha256,
                        language,
                        llm_profile_version_id,
                        image_profile_version_id,
                        video_profile_version_id,
                        image_candidate_count,
                        int(allow_remote_outbound),
                        now,
                        now,
                        "local-user",
                    ),
                )
                connection.execute(
                    "INSERT INTO one_sentence_video_run_events (run_id,stage,state,metadata_json,created_at) VALUES (?,?,?,?,?)",
                    (run_id, "RUNTIME_PREFLIGHT", "PLANNING", "{}", now),
                )
        except sqlite3.IntegrityError:
            # A concurrent replay may pass the read check before the first
            # insert commits. The unique command key remains authoritative.
            with self.database.connect() as connection:
                replay = connection.execute(
                    """SELECT id,story_sha256,language,llm_profile_version_id,image_profile_version_id,
                    video_profile_version_id,mode,image_candidate_count,remote_outbound_confirmed
                    FROM one_sentence_video_runs WHERE idempotency_key=?""",
                    (idempotency_key,),
                ).fetchone()
            if replay is None:
                raise
            self._assert_idempotency_match(
                replay,
                story_sha256=story_sha256,
                language=language,
                llm_profile_version_id=llm_profile_version_id,
                image_profile_version_id=image_profile_version_id,
                video_profile_version_id=video_profile_version_id,
                mode=mode,
                image_candidate_count=image_candidate_count,
                allow_remote_outbound=allow_remote_outbound,
            )
            return self.get(str(replay["id"]), reconcile=False)
        try:
            llm_profile = self.profiles.get_version(llm_profile_version_id)
            video_profile = self.profiles.get_version(video_profile_version_id)
            image_profile = self.profiles.get_version(image_profile_version_id) if image_profile_version_id else None
            if llm_profile["status"] != "PUBLISHED" or llm_profile["capability"] != "LLM_STORY_PARSE":
                raise DomainRuleError("LOCAL_LLM_PROFILE_UNAVAILABLE", "请选择已发布的故事规划 LLM Profile")
            required_video_capability = "VIDEO_T2V" if mode == "DIRECT_T2V" else "VIDEO_I2V"
            if video_profile["status"] != "PUBLISHED" or video_profile["capability"] != required_video_capability:
                raise DomainRuleError(
                    "VIDEO_PROFILE_UNAVAILABLE",
                    f"当前模式必须选择已发布的 {required_video_capability} Profile",
                )
            if mode == "KEYFRAME_I2V" and (image_profile is None or image_profile["status"] != "PUBLISHED" or image_profile["capability"] != "IMAGE_CONCEPT"):
                raise DomainRuleError("T2I_PROFILE_UNAVAILABLE", "请选择已发布的 IMAGE_CONCEPT Profile")
            video_workflow_id = str((video_profile.get("execution") or {}).get("workflow", {}).get("id") or "")
            image_workflow_id = str((image_profile.get("execution") or {}).get("workflow", {}).get("id") or "") if image_profile else ""
            if not video_workflow_id or (mode == "KEYFRAME_I2V" and not image_workflow_id):
                raise DomainRuleError("PROFILE_WORKFLOW_REQUIRED", "所选生成 Profile 没有冻结 Workflow")
            video_workflow = self.workflows.get_version(video_workflow_id)
            image_workflow = self.workflows.get_version(image_workflow_id) if image_workflow_id else None
            output_spec = self._video_output_spec(video_workflow)
            image_spec = self._image_output_spec(image_workflow) if image_workflow else None
            runtime = self._runtime_probe()
            authority = self._llm_authority(llm_profile)
            if authority["remote"] and not allow_remote_outbound:
                raise DomainRuleError(
                    "OUTBOUND_CONFIRMATION_REQUIRED",
                    "故事文本将发送到所选远端 Provider；请在描述旁确认本次发送",
                    {"provider": authority["provider"], "model": authority["model"], "content_sent": False},
                )
            self._transition(run_id, "PLANNING", "LLM_EXPANSION", {"remote": authority["remote"]})
            video_plan = self.llm.expand_video_prompt(
                llm_profile_version_id,
                normalized_story,
                allow_remote_outbound=allow_remote_outbound,
                language=language,
                output_spec=output_spec,
            )
            camera = self.profiles.resolve_camera_plan(
                video_profile_version_id,
                shot_type=str(video_plan["director_intent"]["shot_type"]),
                movement=str(video_plan["camera_movement"]),
                direction="FORWARD",
                intensity=0.5,
                curve="EASE_IN_OUT",
                prompt_text=str(video_plan["video_prompt"]),
            )
            if not camera["submission_allowed"]:
                raise DomainRuleError("CAMERA_PLAN_UNSUPPORTED", "所选视频 Profile 不支持规划出的运镜")
            plan = {
                "schema_version": "localdrama.one-sentence-video-run-plan.v2",
                "mode": mode,
                "story": normalized_story,
                "language": language,
                "video_plan": video_plan,
                "camera_resolution": camera,
                "output_spec": output_spec,
                "image_spec": image_spec,
                "image_candidate_count": image_candidate_count if mode == "KEYFRAME_I2V" else 0,
                "llm": {**authority, "title": llm_profile["title"], "profile_version_id": llm_profile_version_id},
                "image": {
                    "title": image_profile["title"],
                    "profile_version_id": image_profile_version_id,
                    "workflow_version_id": image_workflow_id,
                    "workflow_title": image_workflow["title"],
                }
                if image_profile and image_workflow
                else None,
                "video": {
                    "title": video_profile["title"],
                    "capability": required_video_capability,
                    "profile_version_id": video_profile_version_id,
                    "workflow_version_id": video_workflow_id,
                    "workflow_title": video_workflow["title"],
                },
                "runtime": {"status": runtime.get("status", "READY"), "endpoint": runtime.get("endpoint")},
                "production_mutations": [],
                "confirmation_required": True,
            }
            plan_hash = _hash(plan)
            fingerprint = _hash(
                {
                    "video": (video_profile.get("execution") or {}).get("fingerprints", {}).get("execution"),
                    "image": (image_profile.get("execution") or {}).get("fingerprints", {}).get("execution") if image_profile else None,
                }
            )
            self._transition(
                run_id,
                "PLANNED",
                "CONFIRMATION",
                {"plan_hash": plan_hash},
                plan_json=plan,
                plan_hash=plan_hash,
                execution_fingerprint=fingerprint,
                error_json={},
            )
            return self.get(run_id, reconcile=False)
        except Exception as error:
            self._fail(run_id, "PLANNING", error)
            raise

    def _row(self, run_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM one_sentence_video_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise DomainRuleError("ONE_SENTENCE_RUN_NOT_FOUND", "一句话生成 Run 不存在")
        item = dict(row)
        item["story"] = json.loads(str(item.pop("story_json") or "{}"))
        item["plan"] = json.loads(str(item.pop("plan_json") or "{}"))
        item["error"] = json.loads(str(item.pop("error_json") or "{}"))
        item["remote_outbound_confirmed"] = bool(item["remote_outbound_confirmed"])
        return item

    def _candidates(self, run_id: str) -> list[dict[str, Any]]:
        selected_id = str(self._row(run_id).get("selected_candidate_id") or "")
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM one_sentence_video_candidates
                WHERE run_id=? ORDER BY batch_no,ordinal,id""",
                (run_id,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["error"] = json.loads(str(item.pop("error_json") or "{}"))
            item["selected"] = str(item["id"]) == selected_id
            if item.get("job_id"):
                try:
                    item["job"] = self.jobs.get_job(str(item["job_id"]))
                except DomainRuleError:
                    item["job"] = None
            items.append(item)
        return items

    def get(self, run_id: str, *, reconcile: bool = True) -> dict[str, Any]:
        item = self._row(run_id)
        should_reconcile = item["state"] in {"COMMITTING", "GENERATING", "CANCELLING"} or item["stage"] == "PROMOTING"
        if reconcile and item["mode"] == "KEYFRAME_I2V" and item["state"] == "CANCELLING" and not item.get("job_id"):
            self._reconcile_image_cancel(run_id)
            item = self._row(run_id)
        if reconcile and item["mode"] == "KEYFRAME_I2V" and item["stage"] == "IMAGE_GENERATING":
            try:
                self._reconcile_image_candidates(run_id)
            except DomainRuleError as error:
                self._fail(run_id, "IMAGE_GENERATING", error)
            item = self._row(run_id)
        if reconcile and item.get("job_id") and should_reconcile:
            try:
                self._reconcile_job(run_id)
            except DomainRuleError as error:
                self._fail(run_id, "PROMOTING", error)
            item = self._row(run_id)
        item["links"] = self._links(item)
        if item.get("job_id"):
            try:
                item["job"] = self.jobs.get_job(str(item["job_id"]))
            except DomainRuleError:
                item["job"] = None
        item["candidates"] = self._candidates(run_id) if item["mode"] == "KEYFRAME_I2V" else []
        return {"run": item}

    @staticmethod
    def _links(item: dict[str, Any]) -> dict[str, str]:
        project_id, episode_id, shot_id = item.get("project_id"), item.get("episode_id"), item.get("shot_id")
        if not project_id:
            return {}
        links = {"project": f"/projects/{project_id}"}
        if episode_id and shot_id:
            base = f"/projects/{project_id}/episodes/{episode_id}"
            links.update({"generation": f"{base}/generation/{shot_id}", "review": f"{base}/review"})
        return links

    def list_recent(self, limit: int = 8) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT id FROM one_sentence_video_runs ORDER BY updated_at DESC,id DESC LIMIT ?", (max(1, min(limit, 30)),)).fetchall()
        return {"items": [self.get(str(row["id"]), reconcile=True)["run"] for row in rows]}

    def _project_code(self, run_id: str) -> str:
        return f"video_{run_id.replace('-', '')[:16]}"

    def _ensure_prompt_revision(
        self,
        *,
        project_id: str,
        shot_id: str,
        purpose: str,
        title: str,
        content: str,
        structured: dict[str, Any],
    ) -> str:
        with self.database.connect() as connection:
            existing = connection.execute(
                """SELECT pr.id FROM prompts p JOIN prompt_revisions pr ON pr.prompt_id=p.id
                WHERE p.project_id=? AND p.owner_type='SHOT' AND p.owner_id=? AND p.purpose=?
                ORDER BY pr.revision_no DESC,pr.created_at DESC LIMIT 1""",
                (project_id, shot_id, purpose),
            ).fetchone()
        if existing is not None:
            return str(existing["id"])
        prompt = self.prompts.create_prompt(
            project_id,
            "SHOT",
            shot_id,
            purpose,
            title,
            content,
            structured,
        )
        return str(prompt["revision"]["id"])

    def _ensure_image_context(self, run_id: str) -> tuple[str, str]:
        run = self._row(run_id)
        plan = run["plan"]
        project_id, shot_id = str(run.get("project_id") or ""), str(run.get("shot_id") or "")
        if not project_id or not shot_id:
            raise DomainRuleError("ONE_SENTENCE_PROJECT_CONTEXT_REQUIRED", "候选图生成缺少项目或镜头检查点")
        intent_id = run.get("image_intent_id")
        if not intent_id:
            with self.database.connect() as connection:
                existing = connection.execute(
                    """SELECT id FROM generation_intents WHERE project_id=? AND owner_type='SHOT'
                    AND owner_id=? AND purpose='T2I' ORDER BY created_at LIMIT 1""",
                    (project_id, shot_id),
                ).fetchone()
            intent = (
                self.generation.get_intent(str(existing["id"]))
                if existing
                else self.generation.create_intent(
                    project_id,
                    "SHOT",
                    shot_id,
                    "T2I",
                    str(plan["video_plan"]["keyframe_prompt"]),
                )
            )
            intent_id = str(intent["id"])
            self._transition(
                run_id,
                "COMMITTING",
                "IMAGE_INTENT_CREATED",
                {"image_intent_id": intent_id},
                image_intent_id=intent_id,
            )
        prompt_revision_id = run.get("image_prompt_revision_id")
        if not prompt_revision_id:
            prompt_revision_id = self._ensure_prompt_revision(
                project_id=project_id,
                shot_id=shot_id,
                purpose="T2I",
                title=f"{plan['video_plan']['title']} · 首帧候选",
                content=str(plan["video_plan"]["keyframe_prompt"]),
                structured={
                    "source_sentence_sha256": run["story_sha256"],
                    "role": "I2V_KEYFRAME_CANDIDATE",
                    "expanded_by": {
                        "provider": plan["video_plan"]["provider"],
                        "model": plan["video_plan"]["model"],
                    },
                },
            )
            self._transition(
                run_id,
                "COMMITTING",
                "IMAGE_PROMPT_FROZEN",
                {"image_prompt_revision_id": prompt_revision_id},
                image_prompt_revision_id=prompt_revision_id,
            )
        return str(intent_id), str(prompt_revision_id)

    def _submit_image_batch(
        self,
        run_id: str,
        *,
        count: int,
        batch_no: int,
        parent_candidate_id: str | None = None,
    ) -> dict[str, Any]:
        run = self._row(run_id)
        if run["mode"] != "KEYFRAME_I2V":
            raise DomainRuleError("ONE_SENTENCE_IMAGE_MODE_REQUIRED", "直接文生视频 Run 不能创建候选图")
        if not 1 <= count <= 8:
            raise DomainRuleError("ONE_SENTENCE_IMAGE_COUNT_INVALID", "候选图数量必须是 1—8")
        intent_id, prompt_revision_id = self._ensure_image_context(run_id)
        parent: dict[str, Any] | None = None
        if parent_candidate_id:
            with self.database.connect() as connection:
                row = connection.execute(
                    "SELECT * FROM one_sentence_video_candidates WHERE id=? AND run_id=?",
                    (parent_candidate_id, run_id),
                ).fetchone()
            if row is None or not row["variant_id"] or not row["media_version_id"]:
                raise DomainRuleError("ONE_SENTENCE_IMAGE_PARENT_INVALID", "只能从已完成的本 Run 候选图重出")
            parent = dict(row)
        with self.database.connect() as connection:
            used_seeds = {
                int(row["seed"])
                for row in connection.execute(
                    "SELECT seed FROM one_sentence_video_candidates WHERE run_id=?",
                    (run_id,),
                ).fetchall()
            }
        submitted_count = 0
        for ordinal in range(count):
            candidate_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"one-sentence:{run_id}:image:{batch_no}:{ordinal}"))
            with self.database.connect() as connection:
                existing_candidate = connection.execute(
                    "SELECT id,state FROM one_sentence_video_candidates WHERE id=?",
                    (candidate_id,),
                ).fetchone()
            if existing_candidate is not None:
                submitted_count += str(existing_candidate["state"]) != "FAILED"
                continue
            seed = secrets.randbelow(2_147_483_647)
            while seed in used_seeds:
                seed = secrets.randbelow(2_147_483_647)
            used_seeds.add(seed)
            generation_key = f"one-sentence:{run_id}:image:{batch_no}:{ordinal}"
            try:
                with self.database.connect() as connection:
                    existing_job = connection.execute(
                        "SELECT id,subject_id FROM jobs WHERE project_id=? AND idempotency_key=?",
                        (run["project_id"], generation_key),
                    ).fetchone()
                if existing_job is not None:
                    variant_id, job_id = str(existing_job["subject_id"]), str(existing_job["id"])
                    with self.database.connect() as connection:
                        variant = connection.execute(
                            "SELECT explicit_seed FROM generation_variants WHERE id=?",
                            (variant_id,),
                        ).fetchone()
                    if variant is not None and variant["explicit_seed"] is not None:
                        seed = int(variant["explicit_seed"])
                else:
                    if parent is not None:
                        derived = self.generation.derive_variant_plan(
                            str(parent["variant_id"]),
                            "RESAMPLE_NEW_SEED",
                            explicit_seed=seed,
                            branch_reason="一句话首帧候选：基于所选构图使用新 seed 重出",
                        )
                        variant_plan = self._variant_from_draft(derived["draft"])
                        plan_hash = str(derived["plan_hash"])
                    else:
                        variant_plan = VariantPlan(
                            variant_type="BASE",
                            parent_variant_id=None,
                            branch_reason="ONE_SENTENCE_KEYFRAME_CANDIDATE",
                            prompt_revision_id=prompt_revision_id,
                            profile_version_id=str(run["image_profile_version_id"]),
                            parameter_set={
                                "PROMPT": run["plan"]["video_plan"]["keyframe_prompt"],
                                "SEED": seed,
                                "timed_directions": [],
                                "performance_bindings": [],
                                "motion_masks": [],
                            },
                            seed_policy="EXPLICIT",
                            explicit_seed=seed,
                            bindings=(),
                        )
                        preflight = self.generation.preflight_variant(intent_id, variant_plan)
                        if preflight["status"] != "READY":
                            raise DomainRuleError(
                                "ONE_SENTENCE_IMAGE_GENERATION_BLOCKED",
                                "候选图生成预检未通过",
                                {"blockers": preflight["blockers"]},
                            )
                        plan_hash = str(preflight["plan_hash"])
                    result = self.generation.submit_confirmed_variant(
                        intent_id,
                        variant_plan,
                        plan_hash,
                        generation_key,
                    )
                    variant_id, job_id = str(result["variant"]["id"]), str(result["job"]["id"])
                now = _now()
                with self.database.transaction() as connection:
                    connection.execute(
                        """INSERT INTO one_sentence_video_candidates
                        (id,run_id,batch_no,ordinal,state,seed,variant_id,job_id,parent_candidate_id,
                         error_json,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,'{}',?,?)""",
                        (
                            candidate_id,
                            run_id,
                            batch_no,
                            ordinal,
                            "SUBMITTED",
                            seed,
                            variant_id,
                            job_id,
                            parent_candidate_id,
                            now,
                            now,
                        ),
                    )
                submitted_count += 1
            except Exception as error:
                now = _now()
                payload = {
                    "code": getattr(error, "code", type(error).__name__),
                    "message": getattr(error, "message", str(error)),
                }
                with self.database.transaction() as connection:
                    connection.execute(
                        """INSERT OR IGNORE INTO one_sentence_video_candidates
                        (id,run_id,batch_no,ordinal,state,seed,parent_candidate_id,error_json,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (
                            candidate_id,
                            run_id,
                            batch_no,
                            ordinal,
                            "FAILED",
                            seed,
                            parent_candidate_id,
                            _json(payload),
                            now,
                            now,
                        ),
                    )
        if submitted_count == 0:
            raise DomainRuleError("ONE_SENTENCE_IMAGE_BATCH_FAILED", "候选图批次没有成功提交任何任务")
        self._transition(
            run_id,
            "GENERATING",
            "IMAGE_GENERATING",
            {"batch_no": batch_no, "candidate_count": count, "parent_candidate_id": parent_candidate_id},
            error_json={},
        )
        return self.get(run_id, reconcile=False)

    def commit(self, run_id: str) -> dict[str, Any]:
        run = self._row(run_id)
        if run["state"] == "SUCCEEDED" or run.get("job_id"):
            return self.get(run_id)
        if run["mode"] == "KEYFRAME_I2V" and self._candidates(run_id):
            if run.get("selected_image_media_version_id"):
                return self._submit_selected_i2v(run_id)
            return self.get(run_id, reconcile=True)
        if not run.get("plan") or not run.get("plan_hash"):
            raise DomainRuleError("ONE_SENTENCE_PLAN_REQUIRED", "本次 Run 没有可确认的规划")
        if run["state"] == "CANCELLED":
            raise DomainRuleError("ONE_SENTENCE_RUN_CANCELLED", "已取消的 Run 不能提交")
        plan = run["plan"]
        try:
            self._transition(run_id, "COMMITTING", "COMMIT_PREFLIGHT", {}, confirmed_at=run.get("confirmed_at") or _now(), error_json={})
            self._runtime_probe()
            current_video_profile = self.profiles.get_version(str(run["video_profile_version_id"]))
            current_image_profile = self.profiles.get_version(str(run["image_profile_version_id"])) if run.get("image_profile_version_id") else None
            current_fingerprint = _hash(
                {
                    "video": (current_video_profile.get("execution") or {}).get("fingerprints", {}).get("execution"),
                    "image": (current_image_profile.get("execution") or {}).get("fingerprints", {}).get("execution") if current_image_profile else None,
                }
            )
            if run.get("execution_fingerprint") and current_fingerprint != run["execution_fingerprint"]:
                raise DomainRuleError("ONE_SENTENCE_PLAN_STALE", "生成 Profile 执行快照已变化，请重新规划后确认")
            self._ensure_not_cancelled(run_id)
            spec = plan["output_spec"]
            video_plan = plan["video_plan"]
            code = self._project_code(run_id)
            project_id = run.get("project_id")
            if not project_id:
                with self.database.connect() as connection:
                    existing = connection.execute("SELECT id FROM projects WHERE code=?", (code,)).fetchone()
                if existing is not None:
                    project_id = str(existing["id"])
                else:
                    project = self.projects.create_project(
                        code=code,
                        title=str(video_plan["title"] or run["story"]["text"][:60]),
                        episode_count=1,
                        season_count=1,
                        aspect_ratio=str(spec["aspect_ratio"]),
                        fps_num=int(round(float(spec["fps"]) * 1000)),
                        fps_den=1000,
                        width=int(spec["width"]),
                        height=int(spec["height"]),
                        target_duration_ms=int(spec["target_duration_ms"]),
                        primary_language=str(run["language"]),
                        subtitle_mode="NONE",
                        allow_unconfigured_capabilities=False,
                        production_plan={
                            "code": f"{code}_one_sentence",
                            "title": f"{video_plan['title']} · 一句话视频方案",
                            "plan": {
                                "mode": "LOCAL_ONLY",
                                "source": "ONE_SENTENCE_VIDEO_RUN",
                                "output_spec": spec,
                                "primary_language": run["language"],
                                "subtitle_mode": "NONE",
                            },
                        },
                        profile_bindings=[
                            {"capability": "LLM_STORY_PARSE", "profile_version_id": run["llm_profile_version_id"]},
                            *(
                                [{"capability": "IMAGE_CONCEPT", "profile_version_id": run["image_profile_version_id"]}]
                                if run.get("image_profile_version_id")
                                else []
                            ),
                            {
                                "capability": "VIDEO_T2V" if run["mode"] == "DIRECT_T2V" else "VIDEO_I2V",
                                "profile_version_id": run["video_profile_version_id"],
                            },
                        ],
                        # Project creation requires a canonical delivery
                        # target, but this run does not claim to execute a
                        # delivery. Generated media is truthfully promoted to
                        # the project media library by _reconcile_job.
                        delivery_target={
                            "code": f"{code}_master",
                            "title": f"{video_plan['title']} · 项目母版目标",
                            "spec": {"path_rel": "05_delivery", "output_spec": spec, "subtitle_mode": "NONE"},
                        },
                    )
                    project_id = str(project["id"])
                self._transition(run_id, "COMMITTING", "PROJECT_CREATED", {"project_id": project_id}, project_id=project_id)
            self._ensure_not_cancelled(run_id)
            with self.database.connect() as connection:
                episode = connection.execute(
                    """SELECT e.id FROM episodes e JOIN seasons s ON s.id=e.season_id
                    WHERE s.project_id=? ORDER BY s.display_order,e.display_order LIMIT 1""",
                    (project_id,),
                ).fetchone()
            if episode is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "项目没有可用分集")
            episode_id = str(episode["id"])
            if run.get("episode_id") != episode_id:
                self._transition(run_id, "COMMITTING", "EPISODE_RESOLVED", {"episode_id": episode_id}, episode_id=episode_id)
            shot_id = run.get("shot_id")
            if not shot_id:
                with self.database.connect() as connection:
                    existing_shot = connection.execute("SELECT id FROM shots WHERE episode_id=? AND code='SHOT_001'", (episode_id,)).fetchone()
                shot = (
                    self.projects.get_shot(str(existing_shot["id"]))
                    if existing_shot
                    else self.projects.create_shot(episode_id, "SHOT_001", int(spec["target_duration_ms"]), str(video_plan["director_intent"]["shot_type"]))
                )
                shot_id = str(shot["id"])
                self._transition(run_id, "COMMITTING", "SHOT_CREATED", {"shot_id": shot_id}, shot_id=shot_id)
            self._ensure_not_cancelled(run_id)
            camera_plan = dict(plan["camera_resolution"]["camera_plan"])
            director_intent = {**video_plan["director_intent"], "camera_plan": camera_plan, "target_duration_ms": int(spec["target_duration_ms"])}
            with self.database.connect() as connection:
                directed = connection.execute("SELECT status FROM shots WHERE id=?", (shot_id,)).fetchone()
            if directed is None:
                raise DomainRuleError("SHOT_NOT_FOUND", "Run 的镜头检查点已失效")
            if str(directed["status"]) == "DRAFT":
                self.shot_studio.save_draft_revision(shot_id, director_intent, freeze=False)
            with self.database.connect() as connection:
                directed = connection.execute("SELECT status FROM shots WHERE id=?", (shot_id,)).fetchone()
            if directed is not None and str(directed["status"]) == "DIRECTED":
                self.shot_studio.mark_ready_shot(shot_id)
            self._transition(run_id, "COMMITTING", "SHOT_READY", {"shot_id": shot_id})
            if run["mode"] == "KEYFRAME_I2V":
                return self._submit_image_batch(
                    run_id,
                    count=int(run["image_candidate_count"]),
                    batch_no=1,
                )
            intent_id = run.get("intent_id")
            if not intent_id:
                with self.database.connect() as connection:
                    existing_intent = connection.execute(
                        "SELECT id FROM generation_intents WHERE project_id=? AND owner_type='SHOT' AND owner_id=? AND purpose='T2V' ORDER BY created_at LIMIT 1",
                        (project_id, shot_id),
                    ).fetchone()
                intent = (
                    self.generation.get_intent(str(existing_intent["id"]))
                    if existing_intent
                    else self.generation.create_intent(project_id, "SHOT", shot_id, "T2V", str(run["story"]["text"]))
                )
                intent_id = str(intent["id"])
                self._transition(run_id, "COMMITTING", "INTENT_CREATED", {"intent_id": intent_id}, intent_id=intent_id)
            prompt_revision_id = run.get("prompt_revision_id")
            if not prompt_revision_id:
                prompt_revision_id = self._ensure_prompt_revision(
                    project_id=project_id,
                    shot_id=shot_id,
                    purpose="T2V",
                    title=f"{video_plan['title']} · T2V",
                    content=str(video_plan["video_prompt"]),
                    structured={
                        "camera_plan": camera_plan,
                        "source_sentence_sha256": run["story_sha256"],
                        "expanded_by": {
                            "provider": video_plan["provider"],
                            "model": video_plan["model"],
                            "provider_connection_id": video_plan.get("provider_connection_id"),
                        },
                    },
                )
                self._transition(run_id, "COMMITTING", "PROMPT_FROZEN", {"prompt_revision_id": prompt_revision_id}, prompt_revision_id=prompt_revision_id)
            seed = int(run.get("seed") or secrets.randbelow(2_147_483_647))
            generation_key = f"one-sentence:{run_id}:base"
            with self.database.connect() as connection:
                existing_job = connection.execute(
                    "SELECT id,subject_id FROM jobs WHERE project_id=? AND idempotency_key=? ORDER BY created_at LIMIT 1",
                    (project_id, generation_key),
                ).fetchone()
                existing_variant = (
                    connection.execute(
                        "SELECT explicit_seed FROM generation_variants WHERE id=?",
                        (existing_job["subject_id"],),
                    ).fetchone()
                    if existing_job is not None
                    else None
                )
            if existing_job is not None:
                self._transition(
                    run_id,
                    "GENERATING",
                    "GENERATING",
                    {"recovered_submission": True},
                    variant_id=str(existing_job["subject_id"]),
                    job_id=str(existing_job["id"]),
                    seed=int(existing_variant["explicit_seed"]) if existing_variant and existing_variant["explicit_seed"] is not None else seed,
                )
                return self.get(run_id)
            parameter_set = {
                "PROMPT": video_plan["video_prompt"],
                "SEED": seed,
                "camera_plan": camera_plan,
                "timed_directions": [],
                "performance_bindings": [],
                "motion_masks": [],
            }
            variant_plan = VariantPlan(
                variant_type="BASE",
                parent_variant_id=None,
                branch_reason="ONE_SENTENCE_VIDEO_RUN",
                prompt_revision_id=prompt_revision_id,
                profile_version_id=str(run["video_profile_version_id"]),
                parameter_set=parameter_set,
                seed_policy="EXPLICIT",
                explicit_seed=seed,
                bindings=(),
            )
            preflight = self.generation.preflight_variant(intent_id, variant_plan)
            if preflight["status"] != "READY":
                raise DomainRuleError("ONE_SENTENCE_GENERATION_BLOCKED", "生成预检未通过", {"blockers": preflight["blockers"]})
            submitted = self.generation.submit_confirmed_variant(intent_id, variant_plan, str(preflight["plan_hash"]), generation_key)
            variant_id = str(submitted["variant"]["id"])
            job_id = str(submitted["job"]["id"])
            self._transition(
                run_id,
                "GENERATING",
                "GENERATING",
                {"variant_id": variant_id, "job_id": job_id},
                variant_id=variant_id,
                job_id=job_id,
                seed=seed,
            )
            return self.get(run_id)
        except Exception as error:
            current = self._row(run_id)
            if current["state"] != "CANCELLED":
                self._fail(run_id, str(current.get("stage") or "COMMITTING"), error)
            raise

    def _reconcile_image_candidates(self, run_id: str) -> None:
        run = self._row(run_id)
        candidates = self._candidates(run_id)
        if not candidates:
            raise DomainRuleError("ONE_SENTENCE_IMAGE_CANDIDATES_MISSING", "候选图批次没有持久化任务")
        active = 0
        ready = 0
        for candidate in candidates:
            if candidate["state"] == "READY" and candidate.get("media_version_id"):
                ready += 1
                continue
            if candidate["state"] == "FAILED" or not candidate.get("job_id"):
                continue
            job = candidate.get("job") or self.jobs.get_job(str(candidate["job_id"]))
            job_state = str(job["state"])
            now = _now()
            if job_state in ACTIVE_JOB_STATES:
                active += 1
                with self.database.transaction() as connection:
                    connection.execute(
                        """UPDATE one_sentence_video_candidates SET state=?,updated_at=?,revision=revision+1
                        WHERE id=? AND state!=?""",
                        (job_state, now, candidate["id"], job_state),
                    )
                continue
            if job_state == "SUCCEEDED":
                media_version_id = candidate.get("media_version_id")
                if not media_version_id:
                    artifacts = [
                        artifact for attempt in job.get("attempts", []) for artifact in attempt.get("artifacts", []) if artifact.get("status") == "VERIFIED"
                    ]
                    promotion_error: DomainRuleError | None = None
                    for artifact in artifacts:
                        try:
                            promoted = self.media.promote_job_artifact(
                                str(artifact["id"]),
                                purpose="GENERATED_KEYFRAME_SOURCE",
                                media_kind="IMAGE",
                                stage="PROXY",
                                actor="one-sentence-run",
                            )
                            keyframe = self.media.create_keyframe_candidate(
                                str(promoted["media_version_id"]),
                                str(run["shot_id"]),
                                actor="one-sentence-run",
                            )
                            media_version_id = str(keyframe["media_version_id"])
                            break
                        except DomainRuleError as error:
                            promotion_error = error
                    if not media_version_id:
                        raise promotion_error or DomainRuleError(
                            "ONE_SENTENCE_IMAGE_ARTIFACT_MISSING",
                            "成功的候选图任务没有可入库的图片产物",
                        )
                thumbnail_job = self._ensure_derivative(
                    media_version_id,
                    "THUMBNAIL",
                    size="small",
                    frame="poster",
                )
                thumbnail_state = str(thumbnail_job["state"])
                if thumbnail_state not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                    with self.database.transaction() as connection:
                        connection.execute(
                            """UPDATE one_sentence_video_candidates SET state='PROCESSING',media_version_id=?,
                            error_json='{}',updated_at=?,revision=revision+1 WHERE id=?""",
                            (media_version_id, now, candidate["id"]),
                        )
                    active += 1
                    continue
                if thumbnail_state != "SUCCEEDED":
                    error = {
                        "code": thumbnail_job.get("last_error_code") or f"THUMBNAIL_{thumbnail_state}",
                        "message": thumbnail_job.get("last_error_detail_redacted") or "候选图预览生成失败",
                    }
                    with self.database.transaction() as connection:
                        connection.execute(
                            """UPDATE one_sentence_video_candidates SET state='FAILED',media_version_id=?,
                            error_json=?,updated_at=?,revision=revision+1 WHERE id=?""",
                            (media_version_id, _json(error), now, candidate["id"]),
                        )
                    continue
                with self.database.transaction() as connection:
                    connection.execute(
                        """UPDATE one_sentence_video_candidates SET state='READY',media_version_id=?,
                        error_json='{}',updated_at=?,revision=revision+1 WHERE id=?""",
                        (media_version_id, now, candidate["id"]),
                    )
                ready += 1
                continue
            error = {
                "code": job.get("last_error_code") or f"JOB_{job_state}",
                "message": job.get("last_error_detail_redacted") or "候选图任务未成功完成",
                "job_state": job_state,
            }
            with self.database.transaction() as connection:
                connection.execute(
                    """UPDATE one_sentence_video_candidates SET state='FAILED',error_json=?,
                    updated_at=?,revision=revision+1 WHERE id=?""",
                    (_json(error), now, candidate["id"]),
                )
        if active:
            return
        if ready:
            if run["state"] != "AWAITING_SELECTION" or run["stage"] != "IMAGE_SELECTION":
                self._transition(
                    run_id,
                    "AWAITING_SELECTION",
                    "IMAGE_SELECTION",
                    {"ready_candidates": ready, "total_candidates": len(candidates)},
                    error_json={},
                )
            return
        raise DomainRuleError("ONE_SENTENCE_IMAGE_BATCH_FAILED", "本批候选图全部生成失败，可重新提交一批")

    def _sync_cancelled_candidates(self, run_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE one_sentence_video_candidates SET state='CANCELLED',updated_at=?,revision=revision+1
                WHERE run_id=? AND state IN ('SUBMITTED','QUEUED','CLAIMED','RETRY_WAIT','PROCESSING')
                AND job_id IN (SELECT id FROM jobs WHERE state='CANCELLED')""",
                (_now(), run_id),
            )

    def _reconcile_image_cancel(self, run_id: str) -> None:
        active = 0
        for candidate in self._candidates(run_id):
            job = candidate.get("job")
            if job and str(job["state"]) in ACTIVE_JOB_STATES:
                active += 1
        if active == 0:
            self._sync_cancelled_candidates(run_id)
            self._transition(
                run_id,
                "CANCELLED",
                "CANCELLED",
                {"candidate_jobs_terminal": True},
                completed_at=_now(),
            )

    def _reconcile_job(self, run_id: str) -> None:
        run = self._row(run_id)
        job = self.jobs.get_job(str(run["job_id"]))
        state = str(job["state"])
        if state in ACTIVE_JOB_STATES:
            run_state = "CANCELLING" if state == "CANCEL_REQUESTED" else "GENERATING"
            stage = "CANCELLING" if state == "CANCEL_REQUESTED" else ("VIDEO_GENERATING" if run["mode"] == "KEYFRAME_I2V" else "GENERATING")
            if run["state"] != run_state or run["stage"] != stage:
                self._transition(run_id, run_state, stage, {"job_state": state})
            return
        if state == "SUCCEEDED":
            media_version_id = run.get("media_version_id")
            if not media_version_id:
                verified = [
                    artifact for attempt in job.get("attempts", []) for artifact in attempt.get("artifacts", []) if artifact.get("status") == "VERIFIED"
                ]
                verified.sort(key=lambda item: str(item.get("kind")) not in {"COMFY_OUTPUT", "VIDEO", "GENERATED_VIDEO"})
                promotion_error: DomainRuleError | None = None
                for artifact in verified:
                    try:
                        promoted = self.media.promote_job_artifact(
                            str(artifact["id"]), purpose="SHOT_VIDEO", media_kind="VIDEO", stage="FORMAL", actor="one-sentence-run"
                        )
                        media_version_id = str(promoted["media_version_id"])
                        self.media.submit_default_derivatives(media_version_id)
                        break
                    except DomainRuleError as error:
                        promotion_error = error
                if not media_version_id:
                    raise promotion_error or DomainRuleError("ONE_SENTENCE_VIDEO_ARTIFACT_MISSING", "成功任务没有可入库的视频产物")
            thumbnail_job = self._ensure_derivative(
                str(media_version_id),
                "THUMBNAIL",
                size="small",
                frame="poster",
            )
            thumbnail_state = str(thumbnail_job["state"])
            if thumbnail_state not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                self._transition(
                    run_id,
                    "GENERATING",
                    "VIDEO_POSTPROCESSING",
                    {"job_state": state, "thumbnail_job_id": thumbnail_job["id"]},
                    media_version_id=media_version_id,
                )
                return
            if thumbnail_state != "SUCCEEDED":
                raise DomainRuleError(
                    "ONE_SENTENCE_VIDEO_POSTER_FAILED",
                    "视频已经生成，但本机预览封面处理失败",
                    {"thumbnail_job_id": thumbnail_job["id"], "thumbnail_state": thumbnail_state},
                )
            self._transition(
                run_id,
                "SUCCEEDED",
                "SUCCEEDED",
                {"job_state": state, "media_version_id": media_version_id},
                media_version_id=media_version_id,
                completed_at=_now(),
                error_json={},
            )
            return
        if state == "CANCELLED":
            self._transition(run_id, "CANCELLED", "CANCELLED", {"job_state": state}, completed_at=_now())
            return
        error = {
            "code": job.get("last_error_code") or f"JOB_{state}",
            "message": job.get("last_error_detail_redacted") or "视频生成任务未成功完成",
            "details": {"job_id": job["id"], "job_state": state},
            "retryable": state in {"FAILED", "NEEDS_ATTENTION", "ORPHANED"},
        }
        failure_stage = "VIDEO_GENERATING" if run["mode"] == "KEYFRAME_I2V" else "GENERATING"
        self._transition(run_id, "FAILED", failure_stage, {"job_state": state}, error_json=error)

    def _submit_selected_i2v(self, run_id: str) -> dict[str, Any]:
        run = self._row(run_id)
        plan = run["plan"]
        media_version_id = str(run.get("selected_image_media_version_id") or "")
        candidate_id = str(run.get("selected_candidate_id") or "")
        if not media_version_id or not candidate_id:
            raise DomainRuleError("ONE_SENTENCE_IMAGE_SELECTION_REQUIRED", "请先选择并批准一张候选图")
        project_id, shot_id = str(run["project_id"]), str(run["shot_id"])
        intent_id = run.get("intent_id")
        if not intent_id:
            with self.database.connect() as connection:
                existing = connection.execute(
                    """SELECT id FROM generation_intents WHERE project_id=? AND owner_type='SHOT'
                    AND owner_id=? AND purpose='I2V_PROXY' ORDER BY created_at LIMIT 1""",
                    (project_id, shot_id),
                ).fetchone()
            intent = (
                self.generation.get_intent(str(existing["id"]))
                if existing
                else self.generation.create_intent(
                    project_id,
                    "SHOT",
                    shot_id,
                    "I2V_PROXY",
                    str(run["story"]["text"]),
                )
            )
            intent_id = str(intent["id"])
            self._transition(
                run_id,
                "COMMITTING",
                "VIDEO_INTENT_CREATED",
                {"intent_id": intent_id},
                intent_id=intent_id,
            )
        prompt_revision_id = run.get("prompt_revision_id")
        if not prompt_revision_id:
            prompt_revision_id = self._ensure_prompt_revision(
                project_id=project_id,
                shot_id=shot_id,
                purpose="I2V",
                title=f"{plan['video_plan']['title']} · I2V",
                content=str(plan["video_plan"]["video_prompt"]),
                structured={
                    "camera_plan": plan["camera_resolution"]["camera_plan"],
                    "source_sentence_sha256": run["story_sha256"],
                    "selected_keyframe_media_version_id": media_version_id,
                },
            )
            self._transition(
                run_id,
                "COMMITTING",
                "VIDEO_PROMPT_FROZEN",
                {"prompt_revision_id": prompt_revision_id},
                prompt_revision_id=prompt_revision_id,
            )
        seed = int(run.get("seed") or secrets.randbelow(2_147_483_647))
        generation_key = f"one-sentence:{run_id}:selected:{candidate_id}"
        with self.database.connect() as connection:
            existing_job = connection.execute(
                "SELECT id,subject_id FROM jobs WHERE project_id=? AND idempotency_key=?",
                (project_id, generation_key),
            ).fetchone()
        if existing_job is not None:
            variant_id, job_id = str(existing_job["subject_id"]), str(existing_job["id"])
        else:
            variant_plan = VariantPlan(
                variant_type="BASE",
                parent_variant_id=None,
                branch_reason="ONE_SENTENCE_SELECTED_KEYFRAME_I2V",
                prompt_revision_id=str(prompt_revision_id),
                profile_version_id=str(run["video_profile_version_id"]),
                parameter_set={
                    "PROMPT": plan["video_plan"]["video_prompt"],
                    "SEED": seed,
                    "camera_plan": plan["camera_resolution"]["camera_plan"],
                    "timed_directions": [],
                    "performance_bindings": [],
                    "motion_masks": [],
                },
                seed_policy="EXPLICIT",
                explicit_seed=seed,
                bindings=(VariantInput("FIRST_FRAME", media_version_id, 0, None),),
            )
            preflight = self.generation.preflight_variant(str(intent_id), variant_plan)
            if preflight["status"] != "READY":
                raise DomainRuleError(
                    "ONE_SENTENCE_I2V_BLOCKED",
                    "所选首帧的图生视频预检未通过",
                    {"blockers": preflight["blockers"]},
                )
            submitted = self.generation.submit_confirmed_variant(
                str(intent_id),
                variant_plan,
                str(preflight["plan_hash"]),
                generation_key,
            )
            variant_id, job_id = str(submitted["variant"]["id"]), str(submitted["job"]["id"])
        self._transition(
            run_id,
            "GENERATING",
            "VIDEO_GENERATING",
            {"candidate_id": candidate_id, "media_version_id": media_version_id, "job_id": job_id},
            variant_id=variant_id,
            job_id=job_id,
            seed=seed,
            error_json={},
        )
        return self.get(run_id, reconcile=False)

    def select_image_candidate(
        self,
        run_id: str,
        candidate_id: str,
        *,
        confirm_review_checks: bool,
    ) -> dict[str, Any]:
        run = self._row(run_id)
        if run["mode"] != "KEYFRAME_I2V":
            raise DomainRuleError("ONE_SENTENCE_IMAGE_MODE_REQUIRED", "直接文生视频 Run 没有候选图")
        if not confirm_review_checks:
            raise DomainRuleError(
                "ONE_SENTENCE_KEYFRAME_REVIEW_REQUIRED",
                "转视频前必须显式确认候选图的人物、手部、场景、构图、光线和可视频化检查",
            )
        if run.get("selected_candidate_id"):
            if str(run["selected_candidate_id"]) != candidate_id:
                raise DomainRuleError("ONE_SENTENCE_KEYFRAME_ALREADY_APPROVED", "本 Run 已批准另一张首帧，不能静默替换")
            return self._submit_selected_i2v(run_id) if not run.get("job_id") else self.get(run_id)
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT * FROM one_sentence_video_candidates
                WHERE id=? AND run_id=? AND state='READY' AND media_version_id IS NOT NULL""",
                (candidate_id, run_id),
            ).fetchone()
        if row is None:
            raise DomainRuleError("ONE_SENTENCE_IMAGE_CANDIDATE_NOT_READY", "只能选择本 Run 已完成入库的候选图")
        media_version_id = str(row["media_version_id"])
        self.reviews.ensure_templates(actor="one-sentence-run")
        context = self.reviews.review_context(media_version_id)
        checks = [{"item_id": str(item["id"]), "result": "PASS", "comment": "由用户在一句话候选画廊中显式确认"} for item in context["template"]["items"]]
        review = self.reviews.submit_review(
            media_version_id,
            str(context["template"]["id"]),
            "APPROVED",
            int(context["subject_revision"]),
            checks,
            comment="用户确认该候选图可作为本镜头 I2V 首帧",
            actor="local-user",
        )
        self.reviews.select_version(media_version_id, "KEYFRAME", actor="local-user")
        self._transition(
            run_id,
            "COMMITTING",
            "KEYFRAME_APPROVED",
            {"candidate_id": candidate_id, "media_version_id": media_version_id, "review_id": review["id"]},
            selected_candidate_id=candidate_id,
            selected_image_media_version_id=media_version_id,
            keyframe_review_id=str(review["id"]),
            error_json={},
        )
        return self._submit_selected_i2v(run_id)

    def reroll_images(
        self,
        run_id: str,
        *,
        count: int,
        parent_candidate_id: str | None = None,
    ) -> dict[str, Any]:
        run = self._row(run_id)
        if run["mode"] != "KEYFRAME_I2V":
            raise DomainRuleError("ONE_SENTENCE_IMAGE_MODE_REQUIRED", "直接文生视频 Run 不能重出候选图")
        if run.get("selected_candidate_id"):
            raise DomainRuleError("ONE_SENTENCE_KEYFRAME_ALREADY_APPROVED", "首帧已经批准；如需更换请开始新的规划")
        if run["state"] not in {"AWAITING_SELECTION", "FAILED"}:
            raise DomainRuleError("ONE_SENTENCE_IMAGE_REROLL_NOT_READY", "请等待当前候选图批次结束后再重出")
        self._runtime_probe()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(batch_no),0)+1 AS next_batch FROM one_sentence_video_candidates WHERE run_id=?",
                (run_id,),
            ).fetchone()
        return self._submit_image_batch(
            run_id,
            count=count,
            batch_no=int(row["next_batch"]),
            parent_candidate_id=parent_candidate_id,
        )

    def cancel(self, run_id: str) -> dict[str, Any]:
        run = self._row(run_id)
        if run["state"] in {"SUCCEEDED", "CANCELLED"}:
            return self.get(run_id, reconcile=False)
        if run.get("job_id"):
            job = self.jobs.cancel(str(run["job_id"]))
            state = "CANCELLED" if job["state"] == "CANCELLED" else "CANCELLING"
            self._transition(run_id, state, state, {"job_state": job["state"]}, completed_at=_now() if state == "CANCELLED" else None)
        elif run["mode"] == "KEYFRAME_I2V":
            pending = []
            for candidate in self._candidates(run_id):
                job = candidate.get("job")
                if not job or str(job["state"]) not in ACTIVE_JOB_STATES:
                    continue
                cancelled = self.jobs.cancel(str(candidate["job_id"]))
                pending.append(str(cancelled["state"]) != "CANCELLED")
            # Candidate rows mirror their job's outcome; leaving them QUEUED
            # made the workbench claim generation was still running after the
            # run itself was already cancelled.
            self._sync_cancelled_candidates(run_id)
            state = "CANCELLING" if any(pending) else "CANCELLED"
            self._transition(
                run_id,
                state,
                state,
                {"candidate_jobs": len(pending)},
                completed_at=_now() if state == "CANCELLED" else None,
            )
        else:
            self._transition(run_id, "CANCELLED", "CANCELLED", {"project_id": run.get("project_id")}, completed_at=_now())
        return self.get(run_id, reconcile=False)

    @staticmethod
    def _variant_from_draft(draft: dict[str, Any]) -> VariantPlan:
        return VariantPlan(
            variant_type=str(draft["variant_type"]),
            parent_variant_id=draft.get("parent_variant_id"),
            branch_reason=str(draft["branch_reason"]),
            prompt_revision_id=draft.get("prompt_revision_id"),
            profile_version_id=str(draft["profile_version_id"]),
            parameter_set=dict(draft["parameter_set"]),
            seed_policy=str(draft["seed_policy"]),
            explicit_seed=draft.get("explicit_seed"),
            provider_random_nonce=draft.get("provider_random_nonce"),
            bindings=tuple(
                VariantInput(str(item["role"]), str(item["media_version_id"]), int(item.get("ordinal", 0)), item.get("weight"))
                for item in draft.get("bindings", [])
            ),
        )

    def retry(self, run_id: str, mode: str) -> dict[str, Any]:
        run = self._row(run_id)
        if run["mode"] == "KEYFRAME_I2V" and not run.get("job_id"):
            return self.reroll_images(run_id, count=int(run["image_candidate_count"]))
        if not run.get("job_id") or not run.get("variant_id") or not run.get("intent_id"):
            return self.commit(run_id)
        self._runtime_probe()
        job = self.jobs.get_job(str(run["job_id"]))
        if mode == "SAME_INPUT" and job["state"] in {"FAILED", "NEEDS_ATTENTION", "ORPHANED"}:
            self.jobs.retry(str(run["job_id"]))
            self._transition(run_id, "GENERATING", "GENERATING", {"retry_mode": mode}, retry_count=int(run["retry_count"]) + 1, error_json={})
            return self.get(run_id, reconcile=False)
        operation = "EXACT_REPLAY" if mode == "SAME_INPUT" else "RESAMPLE_NEW_SEED"
        if mode not in {"SAME_INPUT", "NEW_SEED"}:
            raise DomainRuleError("ONE_SENTENCE_RETRY_MODE_INVALID", "重试模式必须是 SAME_INPUT 或 NEW_SEED")
        new_seed = None
        if mode == "NEW_SEED":
            new_seed = secrets.randbelow(2_147_483_647)
            while new_seed == run.get("seed"):
                new_seed = secrets.randbelow(2_147_483_647)
        derived = self.generation.derive_variant_plan(
            str(run["variant_id"]),
            operation,
            explicit_seed=new_seed,
            branch_reason="一句话生成：保持输入重试" if mode == "SAME_INPUT" else "一句话生成：新 seed 变体",
        )
        draft = self._variant_from_draft(derived["draft"])
        retry_no = int(run["retry_count"]) + 1
        submitted = self.generation.submit_confirmed_variant(
            str(run["intent_id"]), draft, str(derived["plan_hash"]), f"one-sentence:{run_id}:retry:{retry_no}:{mode}"
        )
        self._transition(
            run_id,
            "GENERATING",
            "GENERATING",
            {"retry_mode": mode},
            variant_id=str(submitted["variant"]["id"]),
            job_id=str(submitted["job"]["id"]),
            seed=draft.explicit_seed,
            media_version_id=None,
            retry_count=retry_no,
            error_json={},
            completed_at=None,
        )
        return self.get(run_id, reconcile=False)

    def resume(self, run_id: str) -> dict[str, Any]:
        run = self._row(run_id)
        if run["state"] == "CANCELLED":
            raise DomainRuleError("ONE_SENTENCE_RUN_CANCELLED", "已取消的 Run 不能恢复；可从原描述重新规划")
        if run.get("job_id"):
            job = self.jobs.get_job(str(run["job_id"]))
            # A locally-closed job (e.g. the runtime-busy false negatives) is
            # exactly the checkpoint this button promises to recover: requeue
            # the same job instead of returning the unchanged FAILED run.
            if str(job["state"]) in {"FAILED", "NEEDS_ATTENTION", "ORPHANED"}:
                self.jobs.retry(str(run["job_id"]))
                self._transition(run_id, "GENERATING", "GENERATING", {"resume_retried_job": True}, error_json={})
                return self.get(run_id, reconcile=False)
            return self.get(run_id, reconcile=True)
        if run["mode"] == "KEYFRAME_I2V":
            if run.get("selected_image_media_version_id"):
                return self._submit_selected_i2v(run_id)
            if self._candidates(run_id):
                self._reconcile_image_candidates(run_id)
                return self.get(run_id, reconcile=False)
        return self.commit(run_id)

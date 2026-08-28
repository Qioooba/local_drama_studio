"""Standalone quick image/video generation, deliberately outside Projects."""

from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import secrets
import shutil
import socket
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, TypedDict
from urllib.parse import urlparse

from local_drama.application.media import infer_media_kind
from local_drama.application.override_schema import effective_schema, validate_overrides
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.application.ports.quick_generation import (
    ProfileReader,
    QuickGenerationJobPort,
    QuickGenerationLlmPort,
    QuickGenerationMediaPort,
    WorkflowReader,
)
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.network_policy import endpoint_is_remote
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.filesystem.atomic import replace_path

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}
ACTIVE_JOB_STATES = {"QUEUED", "CLAIMED", "RUNNING", "CANCEL_REQUESTED"}


class RouteDefinition(TypedDict):
    image: bool
    video: bool
    video_capability: str | None
    result_kind: str


ROUTE_DEFINITIONS: dict[str, RouteDefinition] = {
    "TEXT_TO_IMAGE": {"image": True, "video": False, "video_capability": None, "result_kind": "IMAGE"},
    "TEXT_TO_VIDEO": {"image": False, "video": True, "video_capability": "VIDEO_T2V", "result_kind": "VIDEO"},
    "TEXT_TO_IMAGE_TO_VIDEO": {"image": True, "video": True, "video_capability": "VIDEO_I2V", "result_kind": "VIDEO"},
}
RUN_MODES = set(ROUTE_DEFINITIONS)
IMAGE_MODES = {mode for mode, definition in ROUTE_DEFINITIONS.items() if definition["image"]}
VIDEO_MODES = {mode for mode, definition in ROUTE_DEFINITIONS.items() if definition["video"]}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _object_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class QuickGenerationService:
    """Owns planning, queueing, candidate choice and standalone outputs.

    The aggregate never creates or references a Project, Season, Episode,
    Shot, GenerationIntent, PromptRevision, or project MediaVersion.
    """

    def __init__(
        self,
        database: DatabaseUnitOfWork,
        settings: Settings,
        *,
        profiles: ProfileReader,
        workflows: WorkflowReader,
        jobs: QuickGenerationJobPort,
        media: QuickGenerationMediaPort,
        llm: QuickGenerationLlmPort,
        runtime_probe: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.profiles = profiles
        self.workflows = workflows
        self.jobs = jobs
        self.media = media
        self.llm = llm
        self._runtime_probe = runtime_probe or self._probe_runtime

    def _probe_runtime(self) -> dict[str, Any]:
        try:
            stats = ComfyClient(
                self.settings.comfy_base_url,
                self.settings.comfy_output_root,
                timeout_seconds=2.0,
                allow_private_network=self.settings.allows_private_network,
            ).system_stats()
        except DomainRuleError as error:
            if (error.details or {}).get("cause") not in {"TimeoutError", "URLError"} or not self._comfy_port_accepts():
                raise DomainRuleError(
                    "QUICK_GENERATION_RUNTIME_UNAVAILABLE",
                    "本机生成 Runtime 当前不可用；尚未发送远端文本，也未创建任务",
                    {"cause": error.code, "remote_llm_contacted": False},
                    suggested_action="启动生产 ComfyUI 与 worker 后重新规划",
                ) from error
            return {"status": "READY", "endpoint": self.settings.comfy_base_url, "busy": True}
        return {"status": "READY", "endpoint": self.settings.comfy_base_url, "system_stats": stats}

    def _comfy_port_accepts(self) -> bool:
        endpoint = urlparse(self.settings.comfy_base_url)
        try:
            with socket.create_connection((str(endpoint.hostname or "127.0.0.1"), int(endpoint.port or 8188)), timeout=1.0):
                return True
        except OSError:
            return False

    @staticmethod
    def _video_output_spec(workflow: dict[str, Any], parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        graph = _object_dict(workflow.get("workflow"))
        width = height = frames = None
        fps: float | None = None
        for node in graph.values():
            if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
                continue
            inputs = _object_dict(node["inputs"])
            if "Video" in str(node.get("class_type") or "") and all(key in inputs for key in ("width", "height", "length")):
                width, height, frames = int(inputs["width"]), int(inputs["height"]), int(inputs["length"])
            if str(node.get("class_type")) == "CreateVideo" and isinstance(inputs.get("fps"), (int, float)):
                fps = float(inputs["fps"])
        overrides = parameters or {}
        width = int(overrides.get("width", width or 0))
        height = int(overrides.get("height", height or 0))
        frames = int(overrides.get("frame_count", frames or 0))
        fps = float(overrides.get("fps", fps or 0))
        if not width or not height or not frames or not fps or fps <= 0:
            raise DomainRuleError("QUICK_GENERATION_OUTPUT_SPEC_UNDECLARED", "所选视频工作流没有可验证的宽高、帧数或帧率")
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
            "source": "RUN_PARAMETERS" if any(key in overrides for key in ("width", "height", "frame_count", "fps")) else "PUBLISHED_WORKFLOW",
            "editable": True,
        }

    @staticmethod
    def _image_output_spec(workflow: dict[str, Any], parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        graph = _object_dict(workflow.get("workflow"))
        width = height = None
        for node in graph.values():
            inputs = node.get("inputs") if isinstance(node, dict) else None
            if isinstance(inputs, dict) and str(node.get("class_type")) in {"EmptyLatentImage", "EmptySD3LatentImage"}:
                if isinstance(inputs.get("width"), int) and isinstance(inputs.get("height"), int):
                    width, height = int(inputs["width"]), int(inputs["height"])
                    break
        overrides = parameters or {}
        width = int(overrides.get("width", width or 0))
        height = int(overrides.get("height", height or 0))
        if not width or not height:
            raise DomainRuleError("QUICK_GENERATION_IMAGE_SPEC_UNDECLARED", "所选图片工作流没有可验证的宽高")
        divisor = math.gcd(width, height)
        return {
            "width": width,
            "height": height,
            "aspect_ratio": f"{width // divisor}:{height // divisor}",
            "source": "RUN_PARAMETERS" if any(key in overrides for key in ("width", "height")) else "PUBLISHED_WORKFLOW",
            "editable": True,
        }

    @staticmethod
    def _validated_parameters(profile: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        execution = _object_dict(profile.get("execution"))
        schema = _object_dict(execution.get("override_schema")) or None
        validation_profile = {**profile, "override_schema": schema or effective_schema(profile)}
        return validate_overrides(parameters, validation_profile, scope="RUN")

    def _llm_authority(self, profile: dict[str, Any]) -> dict[str, Any]:
        execution = _object_dict(profile.get("execution"))
        connection = _object_dict(execution.get("provider_connection"))
        runtime = _object_dict(execution.get("runtime"))
        contract = _object_dict(profile.get("capability_contract"))
        bundle = _object_dict(profile.get("model_bundle"))
        base_url = str(connection.get("base_url") or runtime.get("base_url") or contract.get("base_url") or self.settings.llm_base_url)
        provider = str(connection.get("protocol") or bundle.get("provider") or contract.get("provider") or "OLLAMA_LOOPBACK").upper()
        return {
            "provider_connection_id": execution.get("provider_connection_id"),
            "provider": "OLLAMA_LOOPBACK" if provider == "OLLAMA" else provider,
            "model": str(bundle.get("model") or connection.get("model") or contract.get("model") or ""),
            "base_url": base_url,
            "remote": endpoint_is_remote(base_url),
        }

    def _transition(self, run_id: str, state: str, stage: str, metadata: dict[str, Any] | None = None, **fields: Any) -> None:
        allowed = {
            "plan_json",
            "plan_hash",
            "execution_fingerprint",
            "selected_candidate_id",
            "selected_image_output_id",
            "job_id",
            "output_id",
            "seed",
            "retry_count",
            "error_json",
            "confirmed_at",
            "completed_at",
        }
        if set(fields) - allowed:
            raise RuntimeError(f"unsupported quick-generation checkpoint fields: {sorted(set(fields) - allowed)}")
        now = _now()
        assignments = ["state=?", "stage=?", "updated_at=?", "revision=revision+1"]
        values: list[Any] = [state, stage, now]
        for key, value in fields.items():
            assignments.append(f"{key}=?")
            values.append(_json(value) if key.endswith("_json") and not isinstance(value, str) else value)
        values.append(run_id)
        with self.database.transaction() as connection:
            connection.execute(f"UPDATE quick_generation_runs SET {','.join(assignments)} WHERE id=?", values)
            connection.execute(
                "INSERT INTO quick_generation_events(run_id,stage,state,metadata_json,created_at) VALUES (?,?,?,?,?)",
                (run_id, stage, state, _json(metadata or {}), now),
            )

    def _fail(self, run_id: str, stage: str, error: Exception) -> None:
        payload = {
            "code": getattr(error, "code", type(error).__name__),
            "message": getattr(error, "message", str(error)),
            "details": getattr(error, "details", {}),
            "retryable": stage != "PLANNING",
        }
        self._transition(run_id, "FAILED", stage, {"error_code": payload["code"]}, error_json=payload)

    def plan(
        self,
        *,
        story: str,
        mode: str,
        language: str,
        llm_profile_version_id: str,
        image_profile_version_id: str | None,
        video_profile_version_id: str | None,
        image_candidate_count: int,
        allow_remote_outbound: bool,
        llm_parameters: dict[str, Any] | None = None,
        image_parameters: dict[str, Any] | None = None,
        video_parameters: dict[str, Any] | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_story = story.strip()
        if not 2 <= len(normalized_story) <= 2000:
            raise DomainRuleError("QUICK_GENERATION_DESCRIPTION_REQUIRED", "画面描述必须是 2—2000 个字符")
        if language not in {"zh-CN", "en-US"} or mode not in RUN_MODES or not 1 <= image_candidate_count <= 8:
            raise DomainRuleError("QUICK_GENERATION_INPUT_INVALID", "快捷生成参数无效")
        route = ROUTE_DEFINITIONS.get(mode)
        if route is None:
            raise DomainRuleError("QUICK_GENERATION_ROUTE_INVALID", "请选择文生图、文生视频或文生图再生视频")
        if route["image"] and not image_profile_version_id:
            raise DomainRuleError("T2I_PROFILE_REQUIRED", "当前生成步骤必须选择图片模型")
        if route["video"] and not video_profile_version_id:
            raise DomainRuleError("VIDEO_PROFILE_REQUIRED", "当前生成步骤必须选择视频模型")
        if not route["image"] and (image_profile_version_id or image_parameters):
            raise DomainRuleError("QUICK_GENERATION_IMAGE_MODEL_FORBIDDEN", "文生视频步骤不能携带图片模型或参数")
        if not route["video"] and (video_profile_version_id or video_parameters):
            raise DomainRuleError("QUICK_GENERATION_VIDEO_MODEL_FORBIDDEN", "文生图步骤不能携带视频模型或参数")
        if not idempotency_key or len(idempotency_key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "规划请求必须提供 Idempotency-Key")
        story_sha256 = hashlib.sha256(normalized_story.encode("utf-8")).hexdigest()
        requested_parameters = {
            "llm": dict(llm_parameters or {}),
            "image": dict(image_parameters or {}),
            "video": dict(video_parameters or {}),
        }
        with self.database.connect() as connection:
            existing = connection.execute("SELECT * FROM quick_generation_runs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        expected = (
            story_sha256,
            language,
            llm_profile_version_id,
            str(image_profile_version_id or ""),
            str(video_profile_version_id or ""),
            mode,
            image_candidate_count,
            int(allow_remote_outbound),
            _hash(requested_parameters),
        )
        if existing is not None:
            actual = (
                str(existing["story_sha256"]),
                str(existing["language"]),
                str(existing["llm_profile_version_id"]),
                str(existing["image_profile_version_id"] or ""),
                str(existing["video_profile_version_id"] or ""),
                str(existing["mode"]),
                int(existing["image_candidate_count"]),
                int(existing["remote_outbound_confirmed"]),
                _hash(json.loads(str(existing["model_parameters_json"] or "{}"))),
            )
            if actual != expected:
                raise DomainRuleError("IDEMPOTENCY_KEY_REUSED", "这个 Idempotency-Key 已绑定到另一份快速生成请求")
            return self.get(str(existing["id"]), reconcile=False)
        run_id, now = str(uuid.uuid4()), _now()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """INSERT INTO quick_generation_runs
                    (id,idempotency_key,mode,state,stage,story_json,story_sha256,language,
                     llm_profile_version_id,image_profile_version_id,video_profile_version_id,
                     image_candidate_count,remote_outbound_confirmed,model_parameters_json,created_at,updated_at,created_by)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                        _json(requested_parameters),
                        now,
                        now,
                        "local-user",
                    ),
                )
                connection.execute(
                    "INSERT INTO quick_generation_events(run_id,stage,state,metadata_json,created_at) VALUES (?,?,?,?,?)",
                    (run_id, "RUNTIME_PREFLIGHT", "PLANNING", "{}", now),
                )
        except sqlite3.IntegrityError:
            return self.plan(
                story=story,
                mode=mode,
                language=language,
                llm_profile_version_id=llm_profile_version_id,
                image_profile_version_id=image_profile_version_id,
                video_profile_version_id=video_profile_version_id,
                image_candidate_count=image_candidate_count,
                allow_remote_outbound=allow_remote_outbound,
                llm_parameters=requested_parameters["llm"],
                image_parameters=requested_parameters["image"],
                video_parameters=requested_parameters["video"],
                idempotency_key=idempotency_key,
            )
        try:
            llm_profile = self.profiles.get_version(llm_profile_version_id)
            video_profile = self.profiles.get_version(video_profile_version_id) if video_profile_version_id else None
            image_profile = self.profiles.get_version(image_profile_version_id) if image_profile_version_id else None
            required_video = route["video_capability"]
            if llm_profile["status"] != "PUBLISHED" or llm_profile["capability"] != "LLM_STORY_PARSE":
                raise DomainRuleError("LOCAL_LLM_PROFILE_UNAVAILABLE", "请选择已发布的故事规划能力")
            if route["video"] and (not video_profile or video_profile["status"] != "PUBLISHED" or video_profile["capability"] != required_video):
                raise DomainRuleError("VIDEO_PROFILE_UNAVAILABLE", f"当前步骤需要可执行的 {required_video} 模型路线")
            if route["image"] and (not image_profile or image_profile["status"] != "PUBLISHED" or not str(image_profile["capability"]).startswith("IMAGE_")):
                raise DomainRuleError("T2I_PROFILE_UNAVAILABLE", "当前步骤需要可执行的文生图模型路线")
            resolved_parameters = {
                "llm": self._validated_parameters(llm_profile, requested_parameters["llm"]),
                "image": self._validated_parameters(image_profile, requested_parameters["image"]) if image_profile else {},
                "video": self._validated_parameters(video_profile, requested_parameters["video"]) if video_profile else {},
            }
            video_execution = _object_dict(video_profile.get("execution")) if video_profile else {}
            image_execution = _object_dict(image_profile.get("execution")) if image_profile else {}
            video_workflow_id = str(_object_dict(video_execution.get("workflow")).get("id") or "")
            image_workflow_id = str(_object_dict(image_execution.get("workflow")).get("id") or "")
            if (route["video"] and not video_workflow_id) or (route["image"] and not image_workflow_id):
                raise DomainRuleError("PROFILE_WORKFLOW_REQUIRED", "所选模型没有配置当前动作的可执行工作流")
            video_workflow = self.workflows.get_version(video_workflow_id) if video_workflow_id else None
            image_workflow = self.workflows.get_version(image_workflow_id) if image_workflow_id else None
            video_spec = self._video_output_spec(video_workflow, resolved_parameters["video"]) if video_workflow else None
            image_spec = self._image_output_spec(image_workflow, resolved_parameters["image"]) if image_workflow else None
            output_spec = video_spec or image_spec
            if output_spec is None:
                raise DomainRuleError("QUICK_GENERATION_OUTPUT_SPEC_UNDECLARED", "当前生成步骤没有可验证的输出规格")
            runtime = self._runtime_probe()
            authority = self._llm_authority(llm_profile)
            if authority["remote"] and not allow_remote_outbound:
                raise DomainRuleError("OUTBOUND_CONFIRMATION_REQUIRED", "描述将发送到所选远端规划服务；请确认本次发送")
            self._transition(run_id, "PLANNING", "LLM_EXPANSION", {"remote": authority["remote"]})
            video_plan = self.llm.expand_video_prompt(
                llm_profile_version_id,
                normalized_story,
                allow_remote_outbound=allow_remote_outbound,
                language=language,
                output_spec=output_spec,
                inference_options=resolved_parameters["llm"],
                target_kind=str(route["result_kind"]),
            )
            camera = None
            if video_profile_version_id:
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
                    raise DomainRuleError("CAMERA_PLAN_UNSUPPORTED", "所选视频模型不支持规划出的运镜")
            plan = {
                "schema_version": "localdrama.quick-generation-plan.v1",
                "mode": mode,
                "result_kind": route["result_kind"],
                "story": normalized_story,
                "language": language,
                "video_plan": video_plan,
                "camera_resolution": camera,
                "output_spec": output_spec,
                "image_spec": image_spec,
                "image_candidate_count": image_candidate_count if route["image"] else 0,
                "model_parameters": resolved_parameters,
                "llm": {**authority, "title": llm_profile["title"], "profile_version_id": llm_profile_version_id},
                "image": {
                    "title": image_profile["title"],
                    "capability": image_profile["capability"],
                    "profile_version_id": image_profile_version_id,
                    "workflow_version_id": image_workflow_id,
                    "workflow_title": image_workflow["title"],
                }
                if image_profile and image_workflow
                else None,
                "video": {
                    "title": video_profile["title"],
                    "capability": required_video,
                    "profile_version_id": video_profile_version_id,
                    "workflow_version_id": video_workflow_id,
                    "workflow_title": video_workflow["title"],
                }
                if video_profile and video_workflow
                else None,
                "runtime": {"status": runtime.get("status", "READY"), "endpoint": runtime.get("endpoint")},
                "mutations": ["CREATE_QUICK_GENERATION_RUN"],
                "confirmation_required": True,
            }
            plan_hash = _hash(plan)
            fingerprint = _hash(
                {
                    "video": (video_profile.get("execution") or {}).get("fingerprints", {}).get("execution") if video_profile else None,
                    "image": (image_profile.get("execution") or {}).get("fingerprints", {}).get("execution") if image_profile else None,
                    "parameters": resolved_parameters,
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
            row = connection.execute("SELECT * FROM quick_generation_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise DomainRuleError("QUICK_GENERATION_NOT_FOUND", "快速生成记录不存在")
        item = dict(row)
        item["story"] = json.loads(str(item.pop("story_json") or "{}"))
        item["plan"] = json.loads(str(item.pop("plan_json") or "{}"))
        item["model_parameters"] = json.loads(str(item.pop("model_parameters_json") or "{}"))
        item["error"] = json.loads(str(item.pop("error_json") or "{}"))
        item["remote_outbound_confirmed"] = bool(item["remote_outbound_confirmed"])
        return item

    def _output(self, output_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM quick_generation_outputs WHERE id=?", (output_id,)).fetchone()
        if row is None:
            raise DomainRuleError("QUICK_GENERATION_OUTPUT_NOT_FOUND", "快速生成作品不存在")
        return {
            **dict(row),
            "content_url": f"/api/v1/quick-generation-outputs/{output_id}/content",
            "thumbnail_url": f"/api/v1/quick-generation-outputs/{output_id}/thumbnail?size=small&frame=poster",
        }

    def _candidates(self, run_id: str) -> list[dict[str, Any]]:
        selected_id = str(self._row(run_id).get("selected_candidate_id") or "")
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM quick_generation_candidates WHERE run_id=? ORDER BY batch_no,ordinal,id", (run_id,)).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["error"] = json.loads(str(item.pop("error_json") or "{}"))
            item["selected"] = str(item["id"]) == selected_id
            item["output"] = self._output(str(item["output_id"])) if item.get("output_id") else None
            item["job"] = self.jobs.get_job(str(item["job_id"])) if item.get("job_id") else None
            if item["state"] == "SUBMITTED" and item["job"] and item["job"]["state"] in ACTIVE_JOB_STATES:
                item["state"] = item["job"]["state"]
            items.append(item)
        return items

    def get(self, run_id: str, *, reconcile: bool = True) -> dict[str, Any]:
        item = self._row(run_id)
        if reconcile and item["mode"] in IMAGE_MODES and item["stage"] == "IMAGE_GENERATING":
            self._reconcile_candidates(run_id)
            item = self._row(run_id)
        if reconcile and item.get("job_id") and item["state"] in {"COMMITTING", "GENERATING", "CANCELLING"}:
            self._reconcile_job(run_id)
            item = self._row(run_id)
        item["links"] = {"workspace": f"/quick-create?run={item['id']}"}
        item["output"] = self._output(str(item["output_id"])) if item.get("output_id") else None
        item["selected_image_output"] = self._output(str(item["selected_image_output_id"])) if item.get("selected_image_output_id") else None
        item["job"] = self.jobs.get_job(str(item["job_id"])) if item.get("job_id") else None
        item["candidates"] = self._candidates(run_id) if item["mode"] in IMAGE_MODES else []
        return {"run": item}

    def list_recent(self, limit: int = 12) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT id FROM quick_generation_runs ORDER BY updated_at DESC,id DESC LIMIT ?", (max(1, min(limit, 50)),)).fetchall()
        return {"items": [self.get(str(row["id"]))["run"] for row in rows]}

    def regenerate_prompt(self, run_id: str, target: str, *, idempotency_key: str) -> dict[str, Any]:
        source = self._row(run_id)
        target = target.strip().upper()
        if target not in {"KEYFRAME", "VIDEO"} or (target == "KEYFRAME" and source["mode"] not in IMAGE_MODES):
            raise DomainRuleError("QUICK_GENERATION_PROMPT_TARGET_INVALID", "当前生成路线不支持这项提示词重写")
        if target == "VIDEO" and source["mode"] == "TEXT_TO_IMAGE":
            raise DomainRuleError("QUICK_GENERATION_PROMPT_TARGET_INVALID", "文生图步骤不生成视频提示词")
        planned = self.plan(
            story=str(source["story"]["text"]),
            mode=str(source["mode"]),
            language=str(source["language"]),
            llm_profile_version_id=str(source["llm_profile_version_id"]),
            image_profile_version_id=str(source["image_profile_version_id"]) if source.get("image_profile_version_id") else None,
            video_profile_version_id=str(source["video_profile_version_id"]) if source.get("video_profile_version_id") else None,
            image_candidate_count=int(source["image_candidate_count"]),
            allow_remote_outbound=bool(source["remote_outbound_confirmed"]),
            llm_parameters=dict(source.get("model_parameters", {}).get("llm") or {}),
            image_parameters=dict(source.get("model_parameters", {}).get("image") or {}),
            video_parameters=dict(source.get("model_parameters", {}).get("video") or {}),
            idempotency_key=idempotency_key,
        )["run"]
        expected_origin = {"run_id": run_id, "target": target}
        if planned["plan"].get("regenerated_from") == expected_origin:
            return {"run": planned}
        source_plan, fresh = json.loads(_json(source["plan"])), planned["plan"]
        if target == "KEYFRAME":
            source_plan["video_plan"]["keyframe_prompt"] = fresh["video_plan"]["keyframe_prompt"]
        else:
            old_keyframe, old_title = source_plan["video_plan"].get("keyframe_prompt"), source_plan["video_plan"].get("title")
            source_plan["video_plan"] = fresh["video_plan"]
            source_plan["video_plan"]["keyframe_prompt"], source_plan["video_plan"]["title"] = old_keyframe, old_title
            source_plan["camera_resolution"] = fresh["camera_resolution"]
        source_plan["regenerated_from"] = expected_origin
        next_hash = _hash(source_plan)
        self._transition(str(planned["id"]), "PLANNED", "CONFIRMATION", expected_origin, plan_json=source_plan, plan_hash=next_hash, error_json={})
        return self.get(str(planned["id"]), reconcile=False)

    def _job_snapshot(self, run: dict[str, Any], *, seed: int, image: bool, artifact_id: str | None = None) -> dict[str, Any]:
        plan = run["plan"]
        config = plan["image"] if image else plan["video"]
        stage = "image" if image else "video"
        parameters = dict(plan.get("model_parameters", {}).get(stage) or {})
        prompt = plan["video_plan"]["keyframe_prompt" if image else "video_prompt"]
        effective_configuration = {
            "profile_version_id": config["profile_version_id"],
            "capability": config["capability"],
            "effective_settings": parameters,
            "fingerprint": f"sha256:{_hash({'profile_version_id': config['profile_version_id'], 'parameters': parameters})}",
        }
        semantic_inputs: dict[str, Any] = {"PROMPT": prompt, "SEED": seed}
        camera_resolution = plan.get("camera_resolution")
        if isinstance(camera_resolution, dict) and isinstance(camera_resolution.get("camera_plan"), dict):
            semantic_inputs["camera_plan"] = camera_resolution["camera_plan"]
        snapshot: dict[str, Any] = {
            "quick_generation_run_id": run["id"],
            "workflow_version_id": config["workflow_version_id"],
            "semantic_inputs": semantic_inputs,
            "media_bindings": [],
            "artifact_bindings": [],
            "execution_snapshot": {
                "profile_version_id": config["profile_version_id"],
                "effective_configuration": effective_configuration,
            },
        }
        if artifact_id:
            snapshot["artifact_bindings"] = [{"role": "FIRST_FRAME", "artifact_id": artifact_id}]
        return snapshot

    def _create_job(self, run: dict[str, Any], subject_type: str, subject_id: str, snapshot: dict[str, Any], key: str, profile_id: str) -> dict[str, Any]:
        return self.jobs.create_job(
            None,
            "QUICK_GENERATION",
            subject_type,
            subject_id,
            "GPU_H3",
            snapshot,
            key,
            execution_profile_version_id=profile_id,
            max_attempts=1,
            subject_kind=subject_type,
            scope_kind="QUICK_GENERATION",
            stage_code="QUICK_GENERATION",
        )

    def commit(self, run_id: str) -> dict[str, Any]:
        run = self._row(run_id)
        if run["state"] == "SUCCEEDED" or run.get("job_id"):
            return self.get(run_id)
        if run["mode"] in IMAGE_MODES and self._candidates(run_id):
            return self.get(run_id)
        if run["state"] == "CANCELLED" or not run.get("plan_hash"):
            raise DomainRuleError("QUICK_GENERATION_NOT_COMMITTABLE", "当前快速生成规划不能提交")
        self._runtime_probe()
        self._transition(run_id, "COMMITTING", "COMMIT_PREFLIGHT", {}, confirmed_at=run.get("confirmed_at") or _now(), error_json={})
        if run["mode"] in IMAGE_MODES:
            return self._submit_image_batch(run_id, count=int(run["image_candidate_count"]), batch_no=1)
        seed = int(run.get("seed") or secrets.randbelow(2_147_483_647))
        job = self._create_job(
            run,
            "QUICK_GENERATION_RUN",
            run_id,
            self._job_snapshot(run, seed=seed, image=False),
            f"quick-generation:{run_id}:video:0",
            str(run["video_profile_version_id"]),
        )
        self._transition(run_id, "GENERATING", "VIDEO_GENERATING", {"job_id": job["id"]}, job_id=str(job["id"]), seed=seed)
        return self.get(run_id, reconcile=False)

    def _submit_image_batch(self, run_id: str, *, count: int, batch_no: int, parent_candidate_id: str | None = None) -> dict[str, Any]:
        run = self._row(run_id)
        used = {int(item["seed"]) for item in self._candidates(run_id)}
        submitted = 0
        for ordinal in range(count):
            candidate_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"quick-generation:{run_id}:image:{batch_no}:{ordinal}"))
            seed = secrets.randbelow(2_147_483_647)
            while seed in used:
                seed = secrets.randbelow(2_147_483_647)
            used.add(seed)
            try:
                job = self._create_job(
                    run,
                    "QUICK_GENERATION_CANDIDATE",
                    candidate_id,
                    self._job_snapshot(run, seed=seed, image=True),
                    f"quick-generation:{run_id}:image:{batch_no}:{ordinal}",
                    str(run["image_profile_version_id"]),
                )
                now = _now()
                with self.database.transaction() as connection:
                    connection.execute(
                        """INSERT OR IGNORE INTO quick_generation_candidates
                        (id,run_id,batch_no,ordinal,state,seed,job_id,parent_candidate_id,error_json,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (candidate_id, run_id, batch_no, ordinal, "SUBMITTED", seed, job["id"], parent_candidate_id, "{}", now, now),
                    )
                submitted += 1
            except Exception as error:
                now = _now()
                with self.database.transaction() as connection:
                    connection.execute(
                        """INSERT OR IGNORE INTO quick_generation_candidates
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
                            _json({"code": getattr(error, "code", type(error).__name__), "message": getattr(error, "message", str(error))}),
                            now,
                            now,
                        ),
                    )
        if not submitted:
            raise DomainRuleError("QUICK_GENERATION_IMAGE_BATCH_FAILED", "候选图批次没有成功提交任何任务")
        self._transition(run_id, "GENERATING", "IMAGE_GENERATING", {"batch_no": batch_no, "candidate_count": count}, error_json={})
        return self.get(run_id, reconcile=False)

    def _artifact_for_job(self, job: dict[str, Any]) -> dict[str, Any] | None:
        verified = [artifact for attempt in job.get("attempts", []) for artifact in attempt.get("artifacts", []) if artifact.get("status") == "VERIFIED"]
        verified.sort(key=lambda item: str(item.get("kind")) != "COMFY_OUTPUT")
        return verified[0] if verified else None

    def _register_output(self, run_id: str, artifact: dict[str, Any], *, candidate_id: str | None = None) -> dict[str, Any]:
        with self.database.connect() as connection:
            existing = connection.execute("SELECT * FROM quick_generation_outputs WHERE source_artifact_id=?", (artifact["id"],)).fetchone()
        if existing is not None:
            return dict(existing)
        work_root = self.settings.work_root.resolve()
        source = (work_root / str(artifact["sandbox_rel_path"])).resolve()
        if not source.is_relative_to(work_root) or not source.is_file() or source.is_symlink():
            raise DomainRuleError("QUICK_GENERATION_ARTIFACT_MISSING", "快速生成产物文件不存在或越过工作区")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != str(artifact["sha256"]):
            raise DomainRuleError("QUICK_GENERATION_ARTIFACT_INTEGRITY_FAILED", "快速生成产物校验失败")
        kind = infer_media_kind(source)
        if kind not in {"IMAGE", "VIDEO"}:
            raise DomainRuleError("QUICK_GENERATION_OUTPUT_UNSUPPORTED", "快速生成只登记图片或视频作品")
        output_id = str(uuid.uuid4())
        output_root = (self.settings.data_root / "quick-generations" / run_id).resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        destination = (output_root / f"{output_id}{source.suffix.lower()}").resolve()
        if not destination.is_relative_to(output_root):
            raise DomainRuleError("QUICK_GENERATION_OUTPUT_PATH_INVALID", "快速生成作品路径越界")
        partial = destination.with_name(f".partial-{destination.name}")
        shutil.copyfile(source, partial)
        replace_path(partial, destination)
        rel_path = destination.relative_to(self.settings.data_root.resolve()).as_posix()
        mime = mimetypes.guess_type(destination.name)[0] or ("image/png" if kind == "IMAGE" else "video/mp4")
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """INSERT INTO quick_generation_outputs
                    (id,run_id,candidate_id,media_kind,source_artifact_id,rel_path,mime_type,byte_size,sha256,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (output_id, run_id, candidate_id, kind, artifact["id"], rel_path, mime, destination.stat().st_size, digest, _now()),
                )
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return self._output(output_id)

    def _reconcile_candidates(self, run_id: str) -> None:
        ready = failed = active = 0
        for candidate in self._candidates(run_id):
            if candidate["state"] == "READY":
                ready += 1
                continue
            job = candidate.get("job")
            if not job or job["state"] in ACTIVE_JOB_STATES:
                active += 1
                continue
            if job["state"] == "SUCCEEDED":
                artifact = self._artifact_for_job(job)
                if artifact is None:
                    failed += 1
                    state, error, output_id = "FAILED", {"message": "成功任务没有可登记的图片产物"}, None
                else:
                    output = self._register_output(run_id, artifact, candidate_id=str(candidate["id"]))
                    ready += 1
                    state, error, output_id = "READY", {}, output["id"]
            else:
                failed += 1
                state, output_id = "FAILED", None
                error = {"code": job.get("last_error_code") or f"JOB_{job['state']}", "message": job.get("last_error_detail_redacted") or "候选图生成失败"}
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE quick_generation_candidates SET state=?,output_id=?,error_json=?,updated_at=?,revision=revision+1 WHERE id=?",
                    (state, output_id, _json(error), _now(), candidate["id"]),
                )
        if not active:
            if ready:
                self._transition(run_id, "AWAITING_SELECTION", "IMAGE_SELECTION", {"ready": ready, "failed": failed})
            else:
                self._transition(
                    run_id,
                    "FAILED",
                    "IMAGE_GENERATING",
                    {"failed": failed},
                    error_json={"code": "QUICK_GENERATION_IMAGE_BATCH_FAILED", "message": "本批候选图全部生成失败", "retryable": True},
                )

    def _reconcile_job(self, run_id: str) -> None:
        run = self._row(run_id)
        job = self.jobs.get_job(str(run["job_id"]))
        if job["state"] in ACTIVE_JOB_STATES:
            state = "CANCELLING" if job["state"] == "CANCEL_REQUESTED" else "GENERATING"
            self._transition(run_id, state, "CANCELLING" if state == "CANCELLING" else "VIDEO_GENERATING", {"job_state": job["state"]})
            return
        if job["state"] == "SUCCEEDED":
            artifact = self._artifact_for_job(job)
            if artifact is None:
                self._fail(run_id, "VIDEO_GENERATING", DomainRuleError("QUICK_GENERATION_VIDEO_MISSING", "成功任务没有可登记的视频产物"))
                return
            output = self._register_output(run_id, artifact)
            self._transition(run_id, "SUCCEEDED", "SUCCEEDED", {"output_id": output["id"]}, output_id=output["id"], completed_at=_now(), error_json={})
            return
        if job["state"] == "CANCELLED":
            self._transition(run_id, "CANCELLED", "CANCELLED", {"job_state": job["state"]}, completed_at=_now())
            return
        self._transition(
            run_id,
            "FAILED",
            "VIDEO_GENERATING",
            {"job_state": job["state"]},
            error_json={
                "code": job.get("last_error_code") or f"JOB_{job['state']}",
                "message": job.get("last_error_detail_redacted") or "视频生成任务未成功完成",
                "retryable": True,
            },
        )

    def _submit_selected_i2v(self, run_id: str) -> dict[str, Any]:
        run = self._row(run_id)
        output = self._output(str(run.get("selected_image_output_id") or ""))
        if not output.get("source_artifact_id"):
            raise DomainRuleError("QUICK_GENERATION_KEYFRAME_SOURCE_MISSING", "所选首帧没有可复用的生成产物")
        seed = secrets.randbelow(2_147_483_647)
        job = self._create_job(
            run,
            "QUICK_GENERATION_RUN",
            run_id,
            self._job_snapshot(run, seed=seed, image=False, artifact_id=str(output["source_artifact_id"])),
            f"quick-generation:{run_id}:selected:{run['selected_candidate_id']}",
            str(run["video_profile_version_id"]),
        )
        self._transition(
            run_id,
            "GENERATING",
            "VIDEO_GENERATING",
            {"job_id": job["id"], "candidate_id": run["selected_candidate_id"]},
            job_id=str(job["id"]),
            seed=seed,
            error_json={},
        )
        return self.get(run_id, reconcile=False)

    def select_image_candidate(self, run_id: str, candidate_id: str, *, confirm_review_checks: bool) -> dict[str, Any]:
        run = self._row(run_id)
        if run["mode"] not in IMAGE_MODES or not confirm_review_checks:
            raise DomainRuleError("QUICK_GENERATION_IMAGE_CONFIRMATION_REQUIRED", "必须明确确认要使用的候选图")
        if run.get("selected_candidate_id"):
            if str(run["selected_candidate_id"]) != candidate_id:
                raise DomainRuleError("QUICK_GENERATION_KEYFRAME_LOCKED", "本次生成已锁定另一张首帧")
            if run["mode"] == "TEXT_TO_IMAGE":
                return self.get(run_id)
            return self._submit_selected_i2v(run_id) if not run.get("job_id") else self.get(run_id)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM quick_generation_candidates WHERE id=? AND run_id=? AND state='READY' AND output_id IS NOT NULL", (candidate_id, run_id)
            ).fetchone()
        if row is None:
            raise DomainRuleError("QUICK_GENERATION_CANDIDATE_NOT_READY", "只能选择本次生成中已完成的候选图")
        image_only = run["mode"] == "TEXT_TO_IMAGE"
        self._transition(
            run_id,
            "SUCCEEDED" if image_only else "COMMITTING",
            "SUCCEEDED" if image_only else "KEYFRAME_CONFIRMED",
            {"candidate_id": candidate_id, "checks_confirmed": True},
            selected_candidate_id=candidate_id,
            selected_image_output_id=str(row["output_id"]),
            output_id=str(row["output_id"]) if image_only else None,
            completed_at=_now() if image_only else None,
            error_json={},
        )
        if image_only:
            return self.get(run_id, reconcile=False)
        return self._submit_selected_i2v(run_id)

    def reroll_images(self, run_id: str, *, count: int, parent_candidate_id: str | None = None) -> dict[str, Any]:
        run = self._row(run_id)
        if run["mode"] not in IMAGE_MODES or run.get("selected_candidate_id"):
            raise DomainRuleError("QUICK_GENERATION_IMAGE_REROLL_FORBIDDEN", "当前状态不能重出候选图")
        self._runtime_probe()
        with self.database.connect() as connection:
            row = connection.execute("SELECT COALESCE(MAX(batch_no),0)+1 AS n FROM quick_generation_candidates WHERE run_id=?", (run_id,)).fetchone()
        return self._submit_image_batch(run_id, count=count, batch_no=int(row["n"]), parent_candidate_id=parent_candidate_id)

    def cancel(self, run_id: str) -> dict[str, Any]:
        run = self._row(run_id)
        if run["state"] in {"SUCCEEDED", "CANCELLED"}:
            return self.get(run_id, reconcile=False)
        job_ids = (
            [str(run["job_id"])]
            if run.get("job_id")
            else [str(item["job_id"]) for item in self._candidates(run_id) if item.get("job_id") and item.get("job", {}).get("state") in ACTIVE_JOB_STATES]
        )
        pending = False
        for job_id in job_ids:
            pending = self.jobs.cancel(job_id)["state"] != "CANCELLED" or pending
        if run["mode"] in IMAGE_MODES and not run.get("job_id"):
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE quick_generation_candidates SET state='CANCELLED',updated_at=?,revision=revision+1 WHERE run_id=? AND state NOT IN ('READY','FAILED')",
                    (_now(), run_id),
                )
        state = "CANCELLING" if pending else "CANCELLED"
        self._transition(run_id, state, state, {"cancelled_jobs": len(job_ids)}, completed_at=_now() if state == "CANCELLED" else None)
        return self.get(run_id, reconcile=False)

    def retry(self, run_id: str, mode: str) -> dict[str, Any]:
        run = self._row(run_id)
        if run["mode"] in IMAGE_MODES and not run.get("job_id"):
            return self.reroll_images(run_id, count=int(run["image_candidate_count"]))
        if not run.get("job_id"):
            return self.commit(run_id)
        if mode not in {"SAME_INPUT", "NEW_SEED"}:
            raise DomainRuleError("QUICK_GENERATION_RETRY_MODE_INVALID", "重试方式无效")
        job = self.jobs.get_job(str(run["job_id"]))
        if mode == "SAME_INPUT" and job["state"] in {"FAILED", "NEEDS_ATTENTION", "ORPHANED"}:
            self.jobs.retry(str(run["job_id"]))
            self._transition(run_id, "GENERATING", "VIDEO_GENERATING", {"retry_mode": mode}, retry_count=int(run["retry_count"]) + 1, error_json={})
            return self.get(run_id, reconcile=False)
        seed = secrets.randbelow(2_147_483_647)
        while seed == run.get("seed"):
            seed = secrets.randbelow(2_147_483_647)
        snapshot = dict(job["input_snapshot"])
        snapshot["semantic_inputs"] = {**dict(snapshot.get("semantic_inputs") or {}), "SEED": seed}
        retry_no = int(run["retry_count"]) + 1
        next_job = self._create_job(
            run, "QUICK_GENERATION_RUN", run_id, snapshot, f"quick-generation:{run_id}:retry:{retry_no}:{mode}", str(run["video_profile_version_id"])
        )
        self._transition(
            run_id,
            "GENERATING",
            "VIDEO_GENERATING",
            {"retry_mode": mode},
            job_id=str(next_job["id"]),
            seed=seed,
            retry_count=retry_no,
            output_id=None,
            error_json={},
            completed_at=None,
        )
        return self.get(run_id, reconcile=False)

    def resume(self, run_id: str) -> dict[str, Any]:
        run = self._row(run_id)
        if run["state"] == "CANCELLED":
            raise DomainRuleError("QUICK_GENERATION_CANCELLED", "已取消的生成不能恢复；可从原描述重新规划")
        if run.get("job_id"):
            job = self.jobs.get_job(str(run["job_id"]))
            if job["state"] in {"FAILED", "NEEDS_ATTENTION", "ORPHANED"}:
                self.jobs.retry(str(run["job_id"]))
                self._transition(run_id, "GENERATING", "VIDEO_GENERATING", {"resume_retried_job": True}, error_json={})
                return self.get(run_id, reconcile=False)
            return self.get(run_id)
        if run["mode"] in IMAGE_MODES and self._candidates(run_id):
            self._reconcile_candidates(run_id)
            return self.get(run_id, reconcile=False)
        return self.commit(run_id)

    def content_path(self, output_id: str) -> tuple[dict[str, Any], Path]:
        output = self._output(output_id)
        if output.get("legacy_media_version_id"):
            _media, path = self.media.content_path(str(output["legacy_media_version_id"]))
            return output, path
        data_root = self.settings.data_root.resolve()
        path = (data_root / str(output.get("rel_path") or "")).resolve()
        if not path.is_relative_to(data_root) or not path.is_file() or path.is_symlink():
            raise DomainRuleError("QUICK_GENERATION_OUTPUT_FILE_MISSING", "快速生成作品文件不存在或越过数据目录")
        if hashlib.sha256(path.read_bytes()).hexdigest() != str(output["sha256"]):
            raise DomainRuleError("QUICK_GENERATION_OUTPUT_INTEGRITY_FAILED", "快速生成作品完整性校验失败")
        return output, path

    def thumbnail_path(self, output_id: str, size: str = "small", frame: str = "poster") -> tuple[Path, str]:
        output, source = self.content_path(output_id)
        path, mime, _relative, _preset_hash = self.media.cached_visual_thumbnail(
            source,
            cache_namespace=f"quick-generation-{output_id}",
            source_sha256=str(output["sha256"]),
            media_kind=str(output["media_kind"]),
            mime_type=str(output["mime_type"]),
            size=size,
            frame=frame,
        )
        return path, mime

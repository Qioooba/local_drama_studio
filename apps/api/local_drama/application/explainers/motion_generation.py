"""Explainer motion generation: real AI image-to-video clips.

A 解说 (explainer) film's moving pictures are **real image-to-video generations**.
The retired path rendered a still picture and gave it a deterministic FFmpeg
camera push; that is not a video model's output, it cannot be labelled ``I2V``, and
the product no longer offers it at all.  This module is the replacement execution
path: it turns one adopted keyframe into one real generated clip.

Why the generation runs *inside* the stage handler synchronously (and not as a
child job): the worker claims one job at a time, so a stage that waited on its own
child GPU job would deadlock — the same reason the picture runtime works this way.
The stage runs under the worker's lease-heartbeat wrapper, so a long synchronous
step is safe, and the single-GPU exclusivity is taken through the shared
:class:`GpuRuntimeCoordinator` exactly like every other GPU job.

Two rules are enforced here:

* **The published workflow is the contract.**  The semantic inputs are narrowed to
  the slots the resolved workflow version actually declares, and the first frame is
  handed over as the controlled ``{"media_version_id", "sha256"}`` business
  reference — never a path — which the existing V2 input bridge verifies and
  materialises into the ComfyUI input root.
* **No clip is claimed that did not happen.**  A missing profile, an unreachable
  runtime, a rejected prompt or a timeout raises with the real cause; nothing here
  falls back to a still image.
"""

from __future__ import annotations

import hashlib
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from urllib.parse import urlparse

__all__ = [
    "ExplainerMotionGenerationError",
    "ExplainerMotionGenerationRuntime",
    "build_workflow_service",
]

#: Legacy ``execution_profile_versions.capability`` values a per-beat clip may run on.
_VIDEO_CAPABILITIES = ("VIDEO_I2V", "VIDEO_FIRST_FRAME", "VIDEO_FIRST_LAST_FRAME")

#: The requirement key the explainer capability snapshot records for I2V.
_REQUIREMENT = "video.image_to_video"

_VIDEO_SUFFIXES = frozenset({".mp4", ".webm", ".mkv", ".mov", ".m4v"})

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def build_workflow_service(database: Any, settings: Any) -> Any:
    """Port-style factory for the ComfyUI workflow composer (architecture-debt guard)."""

    from local_drama.application.workflows import WorkflowService

    return WorkflowService(database, settings)


class ExplainerMotionGenerationError(RuntimeError):
    """A real clip could not be produced; the beat is reported as blocked."""

    def __init__(self, code: str, message: str, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = dict(detail or {})


class ExplainerMotionGenerationRuntime:
    """Run one local image-to-video generation per explainer beat, in-process."""

    def __init__(
        self,
        database: Any,
        settings: Any,
        *,
        gpu_coordinator: Any | None = None,
        composer: Any | None = None,
        comfy_client_factory: Any | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.database = database
        self.settings = settings
        self.gpu_coordinator = gpu_coordinator
        self._composer = composer
        self._client_factory = comfy_client_factory
        self._sleep = sleep

    # ------------------------------------------------------------------ binding
    def resolve_binding(self, capability_snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Resolve the frozen image-to-video binding for this run.

        The frozen capability snapshot is the authority: whatever profile the
        preflight promised is what gets executed.  Only a profile whose workflow
        version is itself PUBLISHED may run — a profile pointing at a missing or
        unpublished workflow is a visible local misconfiguration, not a reason to
        fall back to a still picture.
        """

        profile_version_id = ""
        entries = (capability_snapshot or {}).get("capabilities") if isinstance(capability_snapshot, Mapping) else None
        if isinstance(entries, Sequence) and not isinstance(entries, (str, bytes)):
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                if str(entry.get("capability") or "") != _REQUIREMENT or not entry.get("available"):
                    continue
                profile_version_id = str(entry.get("profile_version_id") or "")
                break
        placeholders = ",".join("?" for _ in _VIDEO_CAPABILITIES)
        sql = """SELECT p.id AS profile_version_id, p.capability, p.version_no,
                         p.workflow_version_id, w.status AS workflow_status,
                         w.contract_json AS workflow_contract
                    FROM execution_profile_versions p
                    JOIN workflow_versions w ON w.id = p.workflow_version_id
                   WHERE p.status='PUBLISHED' AND w.status='PUBLISHED'
                     AND p.workflow_version_id IS NOT NULL
                     AND {selector}
                   ORDER BY p.version_no DESC, p.updated_at DESC LIMIT 1"""
        with self.database.connect() as connection:
            row = None
            if profile_version_id:
                row = connection.execute(
                    sql.format(selector="p.id = ?"), (profile_version_id,)
                ).fetchone()
            if row is None:
                row = connection.execute(
                    sql.format(selector=f"p.capability IN ({placeholders})"),
                    tuple(_VIDEO_CAPABILITIES),
                ).fetchone()
        if row is None:
            raise ExplainerMotionGenerationError(
                "MOTION_PROFILE_UNAVAILABLE",
                "本机没有已发布且工作流可用的图生视频 Profile，无法生成 AI 动态片段",
                {"capabilities": list(_VIDEO_CAPABILITIES), "requested_profile_version_id": profile_version_id or None},
            )
        profile = dict(row)
        base_url = str(getattr(self.settings, "comfy_base_url", "") or "").strip()
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or (parsed.hostname or "").casefold() not in _LOOPBACK_HOSTS:
            raise ExplainerMotionGenerationError(
                "MOTION_RUNTIME_NOT_LOOPBACK",
                "图生视频运行时必须是本机 loopback HTTP 端点",
                {"base_url_scheme": parsed.scheme, "host": parsed.hostname or ""},
            )
        output_root = getattr(self.settings, "comfy_output_root", None)
        if output_root is None:
            raise ExplainerMotionGenerationError(
                "MOTION_OUTPUT_ROOT_REQUIRED", "图生视频需要配置本地 ComfyUI 输出目录"
            )
        return {
            "profile_version_id": str(profile["profile_version_id"]),
            "capability": str(profile["capability"]),
            "version_no": int(profile["version_no"] or 0),
            "workflow_version_id": str(profile["workflow_version_id"]),
            "base_url": base_url,
            "output_root": str(output_root),
        }

    # ------------------------------------------------------------------ clients
    def _workflows(self) -> Any:
        if self._composer is not None:
            return self._composer
        return build_workflow_service(self.database, self.settings)

    def _client(self, binding: Mapping[str, Any]) -> Any:
        if self._client_factory is not None:
            return self._client_factory(binding)
        from local_drama.infrastructure.comfy import ComfyClient

        return ComfyClient(
            str(binding["base_url"]),
            Path(str(binding["output_root"])),
            allow_private_network=bool(getattr(self.settings, "allows_private_network", False)),
        )

    # ------------------------------------------------------------------ slots
    def _declared_slots(self, workflow_version_id: str) -> set[str]:
        from local_drama.application.workflow_contracts import effective_workflow_contract

        with self.database.connect() as connection:
            effective = effective_workflow_contract(connection, workflow_version_id)
        bindings = effective.get("workflow_bindings") if isinstance(effective, Mapping) else None
        return {str(role) for role in (bindings or {})}

    def _required_node_types(self, workflow_version_id: str) -> set[str]:
        workflow = self._workflows().get_version(workflow_version_id)
        return {
            str(node.get("class_type"))
            for node in dict(workflow.get("workflow") or {}).values()
            if isinstance(node, Mapping) and node.get("class_type")
        }

    # ------------------------------------------------------------------ preflight
    def probe(self, capability_snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Report whether a real image-to-video generation can run, without generating."""

        try:
            binding = self.resolve_binding(capability_snapshot)
        except ExplainerMotionGenerationError as error:
            return {"available": False, "reason": error.code, "detail": error.detail}
        try:
            declared = self._declared_slots(binding["workflow_version_id"])
            required_nodes = self._required_node_types(binding["workflow_version_id"])
        except Exception as error:  # a provider/domain error must not abort the stage
            return {
                "available": False,
                "reason": "MOTION_WORKFLOW_UNAVAILABLE",
                "detail": {
                    "workflow_version_id": binding["workflow_version_id"],
                    "error": type(error).__name__,
                },
                **binding,
            }
        if "FIRST_FRAME" not in declared:
            return {
                "available": False,
                "reason": "MOTION_WORKFLOW_FIRST_FRAME_UNSUPPORTED",
                "detail": {"declared_slots": sorted(declared)},
                **binding,
            }
        try:
            object_info = self._client(binding).object_info()
        except Exception as error:
            return {
                "available": False,
                "reason": "MOTION_RUNTIME_UNREACHABLE",
                "detail": {"base_url": binding["base_url"], "error": type(error).__name__},
                **binding,
            }
        missing = sorted(name for name in sorted(required_nodes) if name not in set(object_info or {}))
        if missing:
            return {"available": False, "reason": "MOTION_NODES_MISSING", "detail": {"missing": missing}, **binding}
        return {
            "available": True,
            "reason": None,
            "detail": {"checked_nodes": len(required_nodes), "declared_slots": sorted(declared)},
            **binding,
        }

    # ------------------------------------------------------------------ gpu lease
    @contextmanager
    def session(self, *, owner_ref: str, on_wait: Any | None = None) -> Iterator[dict[str, Any]]:
        """Hold the single-GPU exclusive lease for a whole generation batch."""

        if self.gpu_coordinator is None:
            yield {}
            return
        from local_drama.application.job_resources import GpuRuntime

        with self.gpu_coordinator.session(
            GpuRuntime.COMFY,
            owner_kind="EXPLAINER_MOTION_GENERATION",
            owner_ref=owner_ref,
            retain_if_same_runtime_waiting=True,
            on_wait=on_wait,
        ) as lease:
            yield dict(lease or {})

    # ------------------------------------------------------------------ generate
    def generate_video(
        self,
        *,
        binding: Mapping[str, Any],
        project_id: str,
        first_frame_media_version_id: str,
        first_frame_sha256: str,
        prompt: str,
        seed: int,
        frames: int,
        output_prefix: str = "local_drama/explainer",
        timeout_seconds: float | None = None,
        negative_prompt: str = "",
    ) -> dict[str, Any]:
        """Generate exactly one video from one verified keyframe and return its file."""

        if not str(prompt).strip():
            raise ExplainerMotionGenerationError("MOTION_PROMPT_EMPTY", "图生视频提示词为空，拒绝生成")
        if not str(first_frame_media_version_id).strip() or not str(first_frame_sha256).strip():
            raise ExplainerMotionGenerationError(
                "MOTION_FIRST_FRAME_REQUIRED", "图生视频必须先采用一张首帧图片"
            )
        workflow_version_id = str(binding["workflow_version_id"])
        declared = self._declared_slots(workflow_version_id)
        semantic_inputs: dict[str, Any] = {
            "FIRST_FRAME": {
                "media_version_id": str(first_frame_media_version_id),
                "sha256": str(first_frame_sha256),
            },
            "PROMPT": str(prompt),
            "SEED": int(seed),
            "FRAME_COUNT": max(1, int(frames)),
            "OUTPUT_PREFIX": str(output_prefix),
        }
        if negative_prompt.strip():
            semantic_inputs["NEGATIVE_PROMPT"] = str(negative_prompt)
        from local_drama.model_platform.application.comfy_artifact_inputs import (
            materialize_v2_comfy_artifact_inputs,
        )

        materialized = materialize_v2_comfy_artifact_inputs(
            self.database,
            self.settings,
            semantic_inputs,
            project_id=project_id,
        )
        # The bridge attaches a receipt under a private key; it is not a workflow slot.
        materialized = {
            role: value for role, value in materialized.items() if str(role) in declared
        }
        missing = sorted(role for role in ("FIRST_FRAME", "PROMPT") if role not in materialized)
        if missing:
            raise ExplainerMotionGenerationError(
                "MOTION_WORKFLOW_SLOT_MISSING",
                "该图生视频 workflow 缺少必需的首帧或提示词槽位",
                {"missing": missing, "declared_slots": sorted(declared)},
            )
        workflows = self._workflows()
        try:
            compiled = workflows.compile_semantic_inputs(workflow_version_id, materialized)
        except Exception as error:
            raise ExplainerMotionGenerationError(
                "MOTION_WORKFLOW_SLOT_UNSUPPORTED",
                "该图生视频 workflow 不接受所需的语义输入",
                {"workflow_version_id": workflow_version_id, "error": type(error).__name__},
            ) from error
        client = self._client(binding)
        deadline = float(
            timeout_seconds
            if timeout_seconds is not None
            else getattr(self.settings, "explainer_video_timeout_seconds", 1800.0) or 1800.0
        )
        started = time.perf_counter()
        try:
            queued = client.queue_prompt(
                compiled["workflow"],
                client_id=f"local-drama-explainer-{str(binding['profile_version_id'])[:8]}",
            )
        except Exception as error:
            raise ExplainerMotionGenerationError(
                "MOTION_PROMPT_REJECTED",
                "ComfyUI 拒绝了图生视频请求",
                {"error": type(error).__name__, "base_url": binding["base_url"]},
            ) from error
        prompt_id = str(queued.get("prompt_id") or "")
        if not prompt_id:
            raise ExplainerMotionGenerationError("MOTION_PROMPT_REJECTED", "ComfyUI 未返回 prompt_id")
        try:
            waited = client.wait_history(prompt_id, timeout_seconds=deadline, poll_seconds=2.0)
        except Exception as error:
            raise ExplainerMotionGenerationError(
                "MOTION_GENERATION_TIMEOUT",
                "图生视频生成超时",
                {"prompt_id": prompt_id, "timeout_seconds": deadline, "error": type(error).__name__},
            ) from error
        history = dict(waited.get("history") or {})
        status = str(waited.get("status") or "")
        if status != "success":
            raise ExplainerMotionGenerationError(
                "MOTION_GENERATION_FAILED",
                "图生视频没有成功完成",
                {"prompt_id": prompt_id, "status": status},
            )
        try:
            outputs = list(client.collect_outputs(history))
        except Exception as error:
            raise ExplainerMotionGenerationError(
                "MOTION_OUTPUT_INVALID",
                "图生视频没有可用的输出文件",
                {"prompt_id": prompt_id, "error": type(error).__name__},
            ) from error
        videos = [path for path in outputs if Path(path).suffix.casefold() in _VIDEO_SUFFIXES]
        if not videos:
            raise ExplainerMotionGenerationError(
                "MOTION_OUTPUT_INVALID",
                "图生视频未产出视频文件",
                {"prompt_id": prompt_id, "outputs": [Path(item).name for item in outputs]},
            )
        video = Path(videos[0])
        if not video.is_file():
            raise ExplainerMotionGenerationError(
                "MOTION_OUTPUT_MISSING", "图生视频输出文件不存在", {"prompt_id": prompt_id}
            )
        return {
            "path": video,
            "sha256": _sha256_file(video),
            "byte_size": video.stat().st_size,
            "prompt_id": prompt_id,
            "workflow_version_id": workflow_version_id,
            "profile_version_id": str(binding["profile_version_id"]),
            "capability": str(binding.get("capability") or ""),
            "seed": int(seed),
            "frames_requested": max(1, int(frames)),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "compiled_hash": str(compiled.get("compiled_hash") or ""),
            "semantic_inputs": {
                role: value for role, value in materialized.items() if role != "FIRST_FRAME"
            },
            "first_frame_media_version_id": str(first_frame_media_version_id),
            "first_frame_sha256": str(first_frame_sha256),
            "network_used": False,
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

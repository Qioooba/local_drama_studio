"""Explainer picture generation: real local images instead of typeset cards.

The explainer's picture track used to be a deterministic FFmpeg/libass card for
every beat.  That is an honest *fallback*, not the product: the beats already
carry a generation-ready prompt (``explainer_visual_beats.prompt_intent``), the
frozen capability snapshot declares ``image.text_to_image`` AVAILABLE, and the
machine runs a real image model.  This module is the missing execution path: it
resolves the bound local model, runs one generation per beat through ComfyUI, and
hands back a file the worker can turn into a moving clip.

Why the generation runs *inside* the stage handler synchronously (and not as a
child job): the worker claims one job at a time, so a stage that waited on its own
child GPU job would deadlock — the same reason the card path existed.  A child-job
submit/collect pair would need a new graph step plus a task-level wait state.  The
explainer stage runs under the worker's lease-heartbeat wrapper, so a long
synchronous step is safe, and the single-GPU exclusivity is taken through the
shared :class:`GpuRuntimeCoordinator` exactly like every other GPU job.
"""

from __future__ import annotations

import hashlib
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

__all__ = [
    "ExplainerPictureGenerationError",
    "ExplainerPictureGenerationRuntime",
    "build_workflow_service",
    "generation_geometry",
]


def build_workflow_service(database: Any, settings: Any) -> Any:
    """Port-style factory for the ComfyUI workflow composer.

    Constructing it inside a runtime method is reported as new cross-service debt,
    so the construction lives in a ``build_*`` scope.  The import stays local: the
    workflow service pulls in the model platform, which this module must not
    require just to describe its geometry.
    """

    from local_drama.application.workflows import WorkflowService

    return WorkflowService(database, settings)


#: Capability names the explainer's ``image.text_to_image`` may be bound to, in
#: preference order.  ``IMAGE_CONCEPT`` is the published Qwen-Image-2.1 T2I profile;
#: ``IMAGE_SCENE`` is the scene variant of the same runtime.
_BINDING_CAPABILITIES = ("IMAGE_CONCEPT", "IMAGE_SCENE")

#: ComfyUI class types any usable text-to-image graph must contain.  Checked against
#: the live ``/object_info`` before the first beat, so an unavailable runtime is one
#: honest preflight failure instead of 52 identical per-beat errors.
_REQUIRED_NODES = ("UNETLoader", "CLIPLoader", "VAELoader", "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage")

#: Qwen-Image-2.1 sizes are declared on a 32-pixel grid.
_SIZE_STEP = 32


class ExplainerPictureGenerationError(RuntimeError):
    """A single generation could not be produced; the caller degrades to a card."""

    def __init__(self, code: str, message: str, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = dict(detail or {})


def generation_geometry(width: int, height: int) -> tuple[int, int]:
    """Round a delivery canvas onto the model's size grid, keeping the aspect ratio.

    The edition canvas is the *delivery* geometry the manifest and the burned
    subtitles are declared against, so it must not change.  The model is asked for
    the nearest grid-aligned canvas of the same aspect ratio and the result is
    scaled to the edition canvas when the clip is built.
    """

    step = _SIZE_STEP
    aligned_w = max(step, int(round(int(width) / step)) * step)
    aligned_h = max(step, int(round(int(height) / step)) * step)
    return aligned_w, aligned_h


class ExplainerPictureGenerationRuntime:
    """Run one local text-to-image generation per explainer beat, in-process."""

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
        """Resolve the frozen image-generation binding for this run.

        The frozen capability snapshot is the authority: whatever profile the
        preflight promised is what gets executed.  Without a snapshot (a standalone
        stage job) the newest PUBLISHED image profile is used, which is the same
        rule the preflight applies.
        """

        profile_version_id = ""
        snapshots = (capability_snapshot or {}).get("capabilities") if isinstance(capability_snapshot, Mapping) else None
        if isinstance(snapshots, Sequence):
            for entry in snapshots:
                if not isinstance(entry, Mapping):
                    continue
                if str(entry.get("capability") or "") in _BINDING_CAPABILITIES and entry.get("available"):
                    profile_version_id = str(entry.get("profile_version_id") or "")
                    break
        placeholders = ",".join("?" for _ in _BINDING_CAPABILITIES)
        with self.database.connect() as connection:
            row = None
            if profile_version_id:
                row = connection.execute(
                    """SELECT pv.*, cd.code AS capability FROM mp_execution_profile_versions pv
                    JOIN mp_capability_definitions cd ON cd.id = pv.capability_definition_id
                    WHERE pv.id = ?""",
                    (profile_version_id,),
                ).fetchone()
            if row is None:
                # The operator's explicit capability assignment is the authority the
                # preflight reads, so the execution path reads the same row.
                row = connection.execute(
                    f"""SELECT pv.*, cd.code AS capability FROM mp_capability_assignments a
                    JOIN mp_capability_definitions cd ON cd.id = a.capability_definition_id
                    JOIN mp_execution_profile_versions pv ON pv.id = a.execution_profile_version_id
                    WHERE cd.code IN ({placeholders}) AND a.execution_profile_version_id IS NOT NULL
                    ORDER BY CASE WHEN a.scope_type = 'SYSTEM' THEN 0 ELSE 1 END, a.revision DESC
                    LIMIT 1""",
                    tuple(_BINDING_CAPABILITIES),
                ).fetchone()
            if row is None:
                row = connection.execute(
                    f"""SELECT pv.*, cd.code AS capability FROM mp_execution_profile_versions pv
                    JOIN mp_capability_definitions cd ON cd.id = pv.capability_definition_id
                    WHERE cd.code IN ({placeholders}) AND pv.workflow_version_id IS NOT NULL
                    ORDER BY pv.version_no DESC LIMIT 1""",
                    tuple(_BINDING_CAPABILITIES),
                ).fetchone()
            if row is None:
                raise ExplainerPictureGenerationError(
                    "PICTURE_PROFILE_UNAVAILABLE",
                    "本机没有已发布的图像生成 Profile，无法生成真实画面",
                    {"capabilities": list(_BINDING_CAPABILITIES)},
                )
            profile = dict(row)
            runtime_row = connection.execute(
                "SELECT * FROM mp_runtime_installation_versions WHERE id=?",
                (str(profile["runtime_installation_version_id"]),),
            ).fetchone()
        if runtime_row is None:
            raise ExplainerPictureGenerationError(
                "PICTURE_RUNTIME_UNAVAILABLE",
                "图像 Profile 绑定的运行时版本不存在",
                {"profile_version_id": str(profile["id"])},
            )
        runtime = dict(runtime_row)
        configuration = runtime.get("configuration_json")
        if isinstance(configuration, str):
            import json

            configuration = json.loads(configuration or "{}")
        configuration = dict(configuration or {})
        base_url = str(configuration.get("base_url") or "").strip()
        if not base_url:
            raise ExplainerPictureGenerationError(
                "PICTURE_RUNTIME_UNAVAILABLE",
                "图像运行时没有声明 loopback 端点",
                {"runtime_installation_version_id": str(runtime["id"])},
            )
        output_root = str(configuration.get("output_root") or "").strip()
        payload = profile.get("payload_json")
        if isinstance(payload, str):
            import json

            payload = json.loads(payload or "{}")
        payload = dict(payload or {})
        return {
            "profile_version_id": str(profile["id"]),
            "capability": str(profile.get("capability") or ""),            "workflow_version_id": str(profile.get("workflow_version_id") or ""),
            "runtime_installation_version_id": str(runtime["id"]),
            "runtime_code": str(configuration.get("runtime_code") or ""),
            "model_code": str(payload.get("model_code") or configuration.get("model_code") or ""),
            "base_url": base_url,
            "output_root": output_root,
        }

    # ------------------------------------------------------------------ clients
    def _client(self, binding: Mapping[str, Any]) -> Any:
        if self._client_factory is not None:
            return self._client_factory(binding)
        from local_drama.infrastructure.comfy import ComfyClient

        output_root = Path(str(binding["output_root"])) if binding.get("output_root") else None
        return ComfyClient(str(binding["base_url"]), output_root, timeout_seconds=60.0, allow_private_network=False)

    def _workflows(self) -> Any:
        if self._composer is not None:
            return self._composer
        return build_workflow_service(self.database, self.settings)

    # ------------------------------------------------------------------ preflight
    def probe(self, capability_snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Report whether real generation can run, without generating anything."""

        try:
            binding = self.resolve_binding(capability_snapshot)
        except ExplainerPictureGenerationError as error:
            return {"available": False, "reason": error.code, "detail": error.detail}
        try:
            workflow = self._workflows().get_version(binding["workflow_version_id"])
        except Exception as error:  # provider/domain errors must not abort the stage
            return {
                "available": False,
                "reason": "PICTURE_WORKFLOW_UNAVAILABLE",
                "detail": {"workflow_version_id": binding["workflow_version_id"], "error": type(error).__name__},
                **binding,
            }
        required = {
            str(node.get("class_type"))
            for node in dict(workflow.get("workflow") or {}).values()
            if isinstance(node, Mapping) and node.get("class_type")
        }
        try:
            object_info = self._client(binding).object_info()
        except Exception as error:
            return {
                "available": False,
                "reason": "PICTURE_RUNTIME_UNREACHABLE",
                "detail": {"base_url": binding["base_url"], "error": type(error).__name__},
                **binding,
            }
        missing = sorted(name for name in sorted(required) if name not in set(object_info or {}))
        if missing:
            return {"available": False, "reason": "PICTURE_NODES_MISSING", "detail": {"missing": missing}, **binding}
        return {"available": True, "reason": None, "detail": {"checked_nodes": len(required)}, **binding}

    # ------------------------------------------------------------------ gpu lease
    @contextmanager
    def session(self, *, owner_ref: str, on_wait: Any | None = None) -> Iterator[dict[str, Any]]:
        """Hold the single-GPU exclusive lease for a whole generation batch.

        One lease for the entire stage is deliberate: the model stays resident
        across beats and other runtimes are evicted once instead of 52 times.
        """

        if self.gpu_coordinator is None:
            yield {}
            return
        from local_drama.application.job_resources import GpuRuntime

        with self.gpu_coordinator.session(
            GpuRuntime.COMFY,
            owner_kind="EXPLAINER_VISUAL_GENERATION",
            owner_ref=owner_ref,
            retain_if_same_runtime_waiting=True,
            on_wait=on_wait,
        ) as lease:
            yield dict(lease or {})

    # ------------------------------------------------------------------ generate
    def generate_image(
        self,
        *,
        binding: Mapping[str, Any],
        prompt: str,
        width: int,
        height: int,
        seed: int,
        negative_prompt: str = "",
        steps: int | None = None,
        output_prefix: str = "local_drama/explainer",
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Generate exactly one image and return its file plus the real evidence."""

        if not str(prompt).strip():
            raise ExplainerPictureGenerationError("PICTURE_PROMPT_EMPTY", "画面提示词为空，拒绝生成")
        gen_width, gen_height = generation_geometry(width, height)
        steps_value = int(steps if steps is not None else getattr(self.settings, "explainer_generation_steps", 20) or 20)
        semantic_inputs: dict[str, Any] = {
            "PROMPT": str(prompt),
            "SEED": int(seed),
            "WIDTH": int(gen_width),
            "HEIGHT": int(gen_height),
            "STEPS": max(1, min(80, steps_value)),
            "OUTPUT_PREFIX": str(output_prefix),
        }
        if negative_prompt.strip():
            semantic_inputs["NEGATIVE_PROMPT"] = str(negative_prompt)
        workflows = self._workflows()
        try:
            compiled = workflows.compile_semantic_inputs(binding["workflow_version_id"], semantic_inputs)
        except Exception as error:
            raise ExplainerPictureGenerationError(
                "PICTURE_WORKFLOW_SLOT_UNSUPPORTED",
                "该图像 workflow 不接受所需的语义输入",
                {"workflow_version_id": binding["workflow_version_id"], "error": type(error).__name__},
            ) from error
        client = self._client(binding)
        deadline = float(timeout_seconds or getattr(self.settings, "explainer_generation_timeout_seconds", 600.0) or 600.0)
        started = time.perf_counter()
        try:
            queued = client.queue_prompt(
                compiled["workflow"],
                client_id=f"local-drama-explainer-{binding['profile_version_id'][:8]}",
            )
        except Exception as error:
            raise ExplainerPictureGenerationError(
                "PICTURE_PROMPT_REJECTED",
                "ComfyUI 拒绝了图像生成请求",
                {"error": type(error).__name__, "base_url": binding["base_url"]},
            ) from error
        prompt_id = str(queued.get("prompt_id") or "")
        if not prompt_id:
            raise ExplainerPictureGenerationError("PICTURE_PROMPT_REJECTED", "ComfyUI 未返回 prompt_id")
        try:
            waited = client.wait_history(prompt_id, timeout_seconds=deadline, poll_seconds=1.0)
        except Exception as error:
            raise ExplainerPictureGenerationError(
                "PICTURE_GENERATION_TIMEOUT",
                "图像生成超时",
                {"prompt_id": prompt_id, "timeout_seconds": deadline, "error": type(error).__name__},
            ) from error
        history = dict(waited.get("history") or {})
        status = str(waited.get("status") or "")
        if status != "success":
            raise ExplainerPictureGenerationError(
                "PICTURE_GENERATION_FAILED",
                "图像生成没有成功完成",
                {"prompt_id": prompt_id, "status": status},
            )
        try:
            outputs = list(client.collect_outputs(history))
        except Exception as error:
            raise ExplainerPictureGenerationError(
                "PICTURE_OUTPUT_INVALID",
                "图像生成没有可用的输出文件",
                {"prompt_id": prompt_id, "error": type(error).__name__},
            ) from error
        images = [path for path in outputs if Path(path).suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp"}]
        if not images:
            raise ExplainerPictureGenerationError(
                "PICTURE_OUTPUT_INVALID",
                "图像生成未产出图片文件",
                {"prompt_id": prompt_id, "outputs": [Path(item).name for item in outputs]},
            )
        image = Path(images[0])
        if not image.is_file():
            raise ExplainerPictureGenerationError(
                "PICTURE_OUTPUT_MISSING", "图像输出文件不存在", {"prompt_id": prompt_id}
            )
        return {
            "path": image,
            "sha256": _sha256_file(image),
            "byte_size": image.stat().st_size,
            "prompt_id": prompt_id,
            "workflow_version_id": str(binding["workflow_version_id"]),
            "profile_version_id": str(binding["profile_version_id"]),
            "runtime_code": str(binding.get("runtime_code") or ""),
            "model_code": str(binding.get("model_code") or ""),
            "generation_width": gen_width,
            "generation_height": gen_height,
            "steps": semantic_inputs["STEPS"],
            "seed": int(seed),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "compiled_hash": str(compiled.get("compiled_hash") or ""),
            "network_used": False,
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

"""Manifest-backed H3 candidate workflow compiler; no automatic Profile activation.

The platform compiles the native MiniMax H3 node chain that this host's
ComfyUI verifiably executes: ComfyUI core ``comfy_extras`` nodes
(``MiniMaxH3ImageToVideo`` + standard samplers) with the loader asset names
frozen in ``model_manifest.json``.  The RH plugin node family is intentionally
not used: on this host it crashes with a Windows access violation inside its
Qwen encoder INT8 unpatch/offload path (``encoder.py::_unload_linear_patcher``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.video_geometry import h3_frame_count, h3_resolution
from local_drama.infrastructure.filesystem.path_policy import canonical_relative_path

# Fallback loader names; manifest ``authoritative_current_state.loader_assets``
# overrides each key when present.  ``ref2va_unet_name`` is only consumed by the
# Ref2V capability (``build_ref2va`` / ``ref2va_supported``); it is deliberately
# absent from ``_H3_MODEL_SUBDIRS`` so the T2V/I2V runtime layout gate keeps its
# exact legacy scope.
_H3_LOADER_ASSETS = {
    "fl2va_unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    "ref2va_unet_name": "minimax_h3_ref2va_int8_convrot.safetensors",
    "text_encoder_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "video_vae_name": "minimax_h3_video_vae_fp16.safetensors",
    "audio_vae_name": "minimax_h3_audio_vae_fp32.safetensors",
    "turbo_lora_name": "minimax_h3_turbo_v4_step600_ema.safetensors",
}

_H3_MODEL_SUBDIRS = {
    "fl2va_unet_name": "diffusion_models",
    "text_encoder_name": "text_encoders",
    "video_vae_name": "vae",
    "audio_vae_name": "vae",
    "turbo_lora_name": "loras",
}

# P1-7 production tiers (Turbo 档位映射).  Frame counts live on the model's
# 17k+5 grid ((n-5)%17==0): 107/124 (~4.46s/~5.17s), then the nearest grid
# values for the ~7s/~8.6s production/master shots are 175/209; resolutions are
# the two verified canvases (9:16 480x832 / 16:9 864x480), and ``default_takes``
# follows the openclaw take-count experience (2/4/6/8/12).  steps/cfg use the
# H3 common values (20 / 1.0, matching the host manifest historical
# step_count evidence); denoise is fine-tuned inside the 0.95-1.0 band per
# tier.  These values are executable compiler inputs: a selected tier changes
# geometry, frame count, scheduler steps and denoise in the generated graph.
# ``default_takes`` is orchestration guidance and is explicitly labelled as
# such in the API payload; it is never presented as a Comfy node parameter.
PRODUCTION_TIERS: dict[str, dict[str, Any]] = {
    "FAST": {
        "code": "FAST",
        "label": "极速粗筛",
        "frames": 107,
        "resolution": {"9:16": (480, 832), "16:9": (864, 480)},
        "denoise": 0.95,
        "steps": 20,
        "cfg": 1.0,
        "default_takes": 2,
    },
    "DRAFT": {
        "code": "DRAFT",
        "label": "主力抽卡",
        "frames": 107,
        "resolution": {"9:16": (480, 832), "16:9": (864, 480)},
        "denoise": 0.97,
        "steps": 20,
        "cfg": 1.0,
        "default_takes": 4,
    },
    "SCREEN": {
        "code": "SCREEN",
        "label": "候选精筛",
        "frames": 124,
        "resolution": {"9:16": (480, 832), "16:9": (864, 480)},
        "denoise": 0.98,
        "steps": 20,
        "cfg": 1.0,
        "default_takes": 6,
    },
    "PRODUCTION": {
        "code": "PRODUCTION",
        "label": "正式成片",
        "frames": 175,
        "resolution": {"9:16": (480, 832), "16:9": (864, 480)},
        "denoise": 1.0,
        "steps": 20,
        "cfg": 1.0,
        "default_takes": 8,
    },
    "MASTER": {
        "code": "MASTER",
        "label": "关键镜头",
        "frames": 209,
        "resolution": {"9:16": (480, 832), "16:9": (864, 480)},
        "denoise": 1.0,
        "steps": 20,
        "cfg": 1.0,
        "default_takes": 12,
    },
}

_H3_DEFAULT_TIER = "DRAFT"


def production_tiers_payload() -> list[dict[str, Any]]:
    """JSON-safe tier table for the UI (tuples normalized to lists)."""
    return [
        {
            **tier,
            "resolution": {aspect: list(size) for aspect, size in tier["resolution"].items()},
            "duration_seconds": round(tier["frames"] / 24.0, 3),
            "effects": {
                "graph": ["width", "height", "frames", "steps", "denoise"],
                "orchestration": ["default_takes"],
                "informational": ["cfg"],
            },
        }
        for tier in PRODUCTION_TIERS.values()
    ]


class H3WorkflowFactory:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _manifest(self) -> dict[str, Any]:
        try:
            manifest = json.loads(self.settings.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DomainRuleError("MANIFEST_INVALID", "H3 workflow 需要有效的本机 model_manifest.json") from error
        if not isinstance(manifest, dict) or not manifest.get("read_only_inventory"):
            raise DomainRuleError("MANIFEST_NOT_READ_ONLY", "模型 manifest 必须是只读 inventory")
        return cast(dict[str, Any], manifest)

    def loader_assets(self) -> dict[str, str]:
        """Loader file names the running Comfy resolves (canonical per manifest)."""
        manifest = self._manifest()
        loader = manifest.get("authoritative_current_state", {}).get("loader_assets", {})
        assets = dict(_H3_LOADER_ASSETS)
        if isinstance(loader, dict):
            for key in assets:
                value = loader.get(key)
                if isinstance(value, str) and value.strip():
                    assets[key] = value.strip()
        return assets

    def candidate_assets(self) -> dict[str, str]:
        assets = self.loader_assets()
        return {
            "model_root": "MiniMax-H3",
            "fl2va_unet_name": assets["fl2va_unet_name"],
            "text_encoder_name": assets["text_encoder_name"],
            "video_vae_name": assets["video_vae_name"],
            "audio_vae_name": assets["audio_vae_name"],
            "turbo_lora_name": assets["turbo_lora_name"],
        }

    def runtime_layout(self) -> dict[str, Any]:
        """Verify the loader assets resolve inside the installed Comfy models tree."""
        manifest = self._manifest()
        comfy_root = Path(str(manifest.get("runtime", {}).get("comfyui_root", "")))
        assets = self.loader_assets()
        canonical = manifest.get("canonical_model_root", {})
        canonical_root = Path(str(canonical.get("path", ""))) if isinstance(canonical, dict) else Path()
        search_roots = [comfy_root / "models"]
        if str(canonical_root):
            search_roots.append(canonical_root)
        search_roots.extend(Path(root) for root in self.settings.model_library_roots)
        unique_roots: list[Path] = []
        for root in search_roots:
            resolved = root.resolve()
            if resolved not in unique_roots:
                unique_roots.append(resolved)
        missing: list[str] = []
        resolved_components: dict[str, str] = {}
        for key, subdir in _H3_MODEL_SUBDIRS.items():
            candidates = [root / subdir / assets[key] for root in unique_roots]
            selected = next((candidate for candidate in candidates if candidate.is_file()), None)
            if selected is None:
                missing.append(str(candidates[0]))
            else:
                resolved_components[key] = str(selected)
        return {
            "status": "PASS" if not missing else "BLOCKED",
            "release_root": str(canonical_root if str(canonical_root) else comfy_root / "models"),
            "search_roots": [str(root) for root in unique_roots],
            "selected_components": self.candidate_assets(),
            "resolved_components": resolved_components,
            "missing_model_files": missing,
            "node_family": "comfy_extras.MiniMaxH3ImageToVideo",
            "manifest_sha256": manifest.get("manifest_sha256"),
        }

    @staticmethod
    def _frame_count(duration_seconds: float) -> int:
        return h3_frame_count(duration_seconds)

    @staticmethod
    def _resolution(aspect_ratio: str) -> tuple[int, int]:
        return h3_resolution(aspect_ratio)

    @staticmethod
    def resolve_tier(tier_code: str, aspect_ratio: str = "auto") -> dict[str, Any]:
        """Resolve a production tier into concrete geometry for the aspect ratio.

        Unknown tiers raise ``H3_TIER_UNSUPPORTED``; unsupported aspect ratios
        raise ``H3_ASPECT_RATIO_UNSUPPORTED`` (both fail closed).  The returned
        dict carries the full tier table plus the resolved ``width``/``height``,
        ``aspect_ratio`` and ``duration_seconds`` (frames at 24 fps).
        """
        code = str(tier_code).strip().upper()
        tier = PRODUCTION_TIERS.get(code)
        if tier is None:
            raise DomainRuleError("H3_TIER_UNSUPPORTED", "不支持的 H3 生产档位", {"tier_code": tier_code})
        width, height = H3WorkflowFactory._resolution(aspect_ratio)
        return {
            **tier,
            "width": width,
            "height": height,
            "aspect_ratio": str(aspect_ratio).strip().lower(),
            "duration_seconds": round(tier["frames"] / 24.0, 3),
        }

    @staticmethod
    def ref2va_supported(manifest: dict[str, Any]) -> bool:
        """Ref2V capability bit: the host manifest carries a ref2va model.

        Reads ``authoritative_current_state.loader_assets.ref2va_unet_name``;
        a non-empty value means the Ref2V *image reference* native chain can be
        compiled (the node family ``MiniMaxH3ReferenceToVideo`` is a core
        comfy_extras builtin on the verified ComfyUI).  The reference *video*
        input chain is separately gated inside ``build_ref2va``.
        """
        state = manifest.get("authoritative_current_state", {})
        loader = state.get("loader_assets", {}) if isinstance(state, dict) else {}
        if not isinstance(loader, dict):
            return False
        value = loader.get("ref2va_unet_name")
        return isinstance(value, str) and bool(value.strip())

    def ref2va_capability(self) -> dict[str, Any]:
        """Full Ref2V capability payload for GET /capabilities/ref2va."""
        manifest = self._manifest()
        supported = self.ref2va_supported(manifest)
        state = manifest.get("authoritative_current_state", {})
        loader = state.get("loader_assets", {}) if isinstance(state, dict) else {}
        route_status = state.get("route_status", {}) if isinstance(state, dict) else {}
        hint: dict[str, Any] = {
            "ref2va_unet_name": loader.get("ref2va_unet_name") if isinstance(loader, dict) else None,
            "route_status": route_status.get("native_ref2v") if isinstance(route_status, dict) else None,
            "video_reference_route": route_status.get("native_v2v") if isinstance(route_status, dict) else None,
            "node_family": "comfy_extras.MiniMaxH3ReferenceToVideo",
            "video_reference_gate": "ref_videos 期望逐帧图像输入，核心 LoadVideo 输出 VIDEO 类型且本机未验证转换链；V2V 在 manifest 中为 static_candidate_only",
        }
        if supported:
            return {
                "capability": "H3_REF2VA_CANDIDATE",
                "supported": True,
                "reason": "本机 manifest 包含 ref2va 模型，参考图 Ref2V 原生链可编译；参考视频输入链本机未验证，提交路径待集成评估。",
                "manifest_hint": hint,
            }
        return {
            "capability": "H3_REF2VA_UNAVAILABLE",
            "supported": False,
            "reason": "本机 model_manifest.json 缺少 ref2va 模型（loader_assets.ref2va_unet_name），Ref2V 能力不可用。",
            "manifest_hint": hint,
        }

    def _validate_common(self, prompt: str, filename_prefix: str) -> None:
        if not prompt.strip():
            raise DomainRuleError("H3_PROMPT_REQUIRED", "H3 prompt 不能为空")
        if canonical_relative_path(filename_prefix, code="H3_OUTPUT_PREFIX_INVALID") != filename_prefix:
            raise DomainRuleError("H3_OUTPUT_PREFIX_INVALID", "H3 输出 prefix 必须是相对路径")

    @staticmethod
    def _normalize_acceleration(value: str) -> str:
        normalized = str(value or "OFF").strip().upper()
        if normalized not in {"OFF", "TURBO_LORA"}:
            raise DomainRuleError("H3_ACCELERATION_UNSUPPORTED", "H3 加速方式仅支持 OFF / TURBO_LORA", {"acceleration": value})
        return normalized

    @staticmethod
    def _apply_runtime_overrides(
        workflow: dict[str, Any],
        *,
        assets: dict[str, str],
        acceleration: str,
        lora_strength: float,
        native_audio: bool,
        audio_vae_node: str,
        audio_decode_node: str,
        create_video_node: str,
        audio_required: bool = False,
    ) -> None:
        """Bind user-visible H3 settings into the immutable Comfy graph."""
        normalized = H3WorkflowFactory._normalize_acceleration(acceleration)
        if normalized == "TURBO_LORA":
            if not 0.0 <= float(lora_strength) <= 2.0:
                raise DomainRuleError("H3_LORA_STRENGTH_INVALID", "H3 LoRA 强度必须在 0—2 之间", {"lora_strength": lora_strength})
            lora_node_id = str(max((int(node_id) for node_id in workflow if str(node_id).isdigit()), default=0) + 1)
            workflow[lora_node_id] = {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {
                    "model": ["1", 0],
                    "lora_name": assets["turbo_lora_name"],
                    "strength_model": float(lora_strength),
                },
            }
            model_ref = [lora_node_id, 0]
            for node in workflow.values():
                if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
                    continue
                if node.get("class_type") in {"BasicScheduler", "BasicGuider"} and node["inputs"].get("model") == ["1", 0]:
                    node["inputs"]["model"] = model_ref
        if not native_audio:
            if audio_required:
                raise DomainRuleError("H3_NATIVE_AUDIO_REQUIRED", "当前 H3 原生参考视频节点要求 Audio VAE，暂不支持关闭原生音频")
            workflow.pop(audio_vae_node, None)
            workflow.pop(audio_decode_node, None)
            create = workflow.get(create_video_node)
            if isinstance(create, dict) and isinstance(create.get("inputs"), dict):
                create["inputs"].pop("audio", None)

    def build_t2va(
        self,
        prompt: str,
        *,
        seed: int,
        duration_seconds: float = 5.0,
        aspect_ratio: str = "16:9",
        filename_prefix: str = "local_drama/h3_proxy",
        sigma_points: int = 50,
        acceleration: str = "off",
        lora_strength: float = 1.0,
        native_audio: bool = True,
        tier: str | None = None,
    ) -> dict[str, Any]:
        """Compile the native MiniMax H3 T2V graph (core comfy_extras nodes).

        ``tier`` (P1-7): when provided, ``resolve_tier`` overrides width/height
        and the frame count (length).  ``duration_seconds`` is still validated
        first (backward compatibility — an out-of-range explicit duration is
        rejected exactly as before), then the tier's frames win over the
        duration-derived length.  Callers that need duration-derived lengths
        must not pass a tier.  Without a tier the compiled graph is byte-for-
        byte identical to the pre-P1-7 behaviour.
        """
        if not 4.0 <= duration_seconds <= 15.0:
            raise DomainRuleError("H3_DURATION_INVALID", "H3 duration 必须在 4—15 秒之间")
        self._validate_common(prompt, filename_prefix)
        assets = self.loader_assets()
        width, height = self._resolution(aspect_ratio)
        length = self._frame_count(duration_seconds)
        if tier is not None:
            resolved = self.resolve_tier(tier, aspect_ratio)
            width, height = resolved["width"], resolved["height"]
            length = resolved["frames"]
        steps = int(sigma_points)
        denoise = 1.0
        if tier is not None:
            resolved = self.resolve_tier(tier, aspect_ratio)
            steps = int(resolved["steps"])
            denoise = float(resolved["denoise"])
        workflow = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": assets["fl2va_unet_name"], "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": assets["text_encoder_name"], "type": "minimax", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": assets["video_vae_name"]}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": assets["audio_vae_name"]}},
            "5": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
            "6": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
            "7": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "scheduler": "simple", "steps": steps, "denoise": denoise}},
            "8": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": prompt, "width": width, "height": height, "length": length}},
            "9": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["8", 0]}},
            "10": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["5", 0], "guider": ["9", 0], "sampler": ["6", 0], "sigmas": ["7", 0], "latent_image": ["8", 1]}},
            "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["3", 0]}},
            "12": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["10", 0], "vae": ["4", 0]}},
            "13": {"class_type": "CreateVideo", "inputs": {"images": ["11", 0], "fps": 24.0, "audio": ["12", 0], "bit_depth": 8}},
            "14": {"class_type": "SaveVideo", "inputs": {"video": ["13", 0], "filename_prefix": filename_prefix, "format": "mp4", "codec": "h264"}},
        }
        self._apply_runtime_overrides(
            workflow,
            assets=assets,
            acceleration=acceleration,
            lora_strength=lora_strength,
            native_audio=native_audio,
            audio_vae_node="4",
            audio_decode_node="12",
            create_video_node="13",
        )
        return workflow

    def build_fl2va(
        self,
        prompt: str,
        *,
        first_frame: str,
        seed: int,
        duration_seconds: float = 5.0,
        aspect_ratio: str = "auto",
        filename_prefix: str = "local_drama/h3_i2v_proxy",
        sigma_points: int = 50,
        acceleration: str = "off",
        lora_strength: float = 1.0,
        native_audio: bool = True,
        tier: str | None = None,
    ) -> dict[str, Any]:
        """Compile the native MiniMax H3 first-frame FL2VA graph.

        ``first_frame`` is a Comfy input-root relative filename, never a host
        path. ComfyGenerationService materializes the verified MediaVersion.

        ``tier`` (P1-7): same precedence as ``build_t2va`` — ``duration_seconds``
        is validated first, then the tier overrides width/height and length.
        """
        if canonical_relative_path(first_frame, code="H3_FIRST_FRAME_INVALID") != first_frame:
            raise DomainRuleError("H3_FIRST_FRAME_INVALID", "H3 首帧必须是隔离 input root 内的相对文件名")
        if not 4.0 <= duration_seconds <= 15.0:
            raise DomainRuleError("H3_DURATION_INVALID", "H3 duration 必须在 4—15 秒之间")
        self._validate_common(prompt, filename_prefix)
        assets = self.loader_assets()
        width, height = self._resolution(aspect_ratio)
        length = self._frame_count(duration_seconds)
        if tier is not None:
            resolved = self.resolve_tier(tier, aspect_ratio)
            width, height = resolved["width"], resolved["height"]
            length = resolved["frames"]
        steps = int(sigma_points)
        denoise = 1.0
        if tier is not None:
            resolved = self.resolve_tier(tier, aspect_ratio)
            steps = int(resolved["steps"])
            denoise = float(resolved["denoise"])
        workflow = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": assets["fl2va_unet_name"], "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": assets["text_encoder_name"], "type": "minimax", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": assets["video_vae_name"]}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": assets["audio_vae_name"]}},
            "5": {"class_type": "LoadImage", "inputs": {"image": first_frame}},
            "6": {"class_type": "ImageScale", "inputs": {"image": ["5", 0], "upscale_method": "lanczos", "width": width, "height": height, "crop": "disabled"}},
            "7": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": prompt, "width": width, "height": height, "length": length, "first_frame": ["6", 0]}},
            "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
            "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
            "10": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "scheduler": "simple", "steps": steps, "denoise": denoise}},
            "11": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["7", 0]}},
            "12": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["8", 0], "guider": ["11", 0], "sampler": ["9", 0], "sigmas": ["10", 0], "latent_image": ["7", 1]}},
            "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}},
            "14": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
            "15": {"class_type": "CreateVideo", "inputs": {"images": ["13", 0], "fps": 24.0, "audio": ["14", 0], "bit_depth": 8}},
            "16": {"class_type": "SaveVideo", "inputs": {"video": ["15", 0], "filename_prefix": filename_prefix, "format": "mp4", "codec": "h264"}},
        }
        self._apply_runtime_overrides(
            workflow,
            assets=assets,
            acceleration=acceleration,
            lora_strength=lora_strength,
            native_audio=native_audio,
            audio_vae_node="4",
            audio_decode_node="14",
            create_video_node="15",
        )
        return workflow

    def build_ref2va(
        self,
        prompt: str,
        *,
        first_frame_media_version_id: str | None = None,
        reference_video_media_version_id: str | None = None,
        seed: int,
        duration_seconds: float = 5.0,
        aspect_ratio: str = "auto",
        filename_prefix: str = "local_drama/h3_ref2v_proxy",
        sigma_points: int = 50,
        acceleration: str = "off",
        lora_strength: float = 1.0,
        native_audio: bool = True,
        tier: str | None = None,
    ) -> dict[str, Any]:
        """Compile the native MiniMax H3 Ref2V graph (P1-8, capability-gated).

        Structure mirrors openclaw ``ref2v.json`` (UNETLoader + CLIPLoader +
        two VAELoaders + reference image → ``MiniMaxH3ReferenceToVideo`` →
        res_multistep/simple sampler chain → VAEDecode/Audio → CreateVideo →
        SaveVideo), adapted to this host's ComfyUI 0.31 comfy_extras node
        schema: the reference image is wired into the ``ref_image_0`` autogrow
        slot and the node additionally requires the ``audio_vae`` loader.

        Gate design (能力位 + 门禁):
        - ``ref2va_supported(manifest)`` is False (no ``ref2va_unet_name`` in
          loader_assets) → ``H3_REF2VA_UNAVAILABLE``.
        - ``reference_video_media_version_id`` is provided → ``H3_REF2VA_UNAVAILABLE``:
          the reference-video input chain is NOT verified on this host
          (``ref_videos`` expects per-frame image inputs, the core ``LoadVideo``
          node outputs a VIDEO type, and the manifest marks V2V as
          ``static_candidate_only``).  The submission path is deferred to
          integration evaluation; only the reference-image compile path is
          offered.
        - no reference at all → ``H3_REF2VA_REFERENCE_REQUIRED``.

        ``first_frame_media_version_id`` follows ``build_fl2va``'s contract: a
        Comfy input-root relative filename.  The future submission integration
        maps a MediaVersion id to the materialized input filename before
        calling this builder (media-version → input-root resolution belongs to
        ComfyGenerationService, not the workflow compiler).
        """
        manifest = self._manifest()
        if not self.ref2va_supported(manifest):
            raise DomainRuleError(
                "H3_REF2VA_UNAVAILABLE",
                "本机 model_manifest.json 缺少 ref2va 模型，Ref2V 原生链无法编译",
                {"missing": "loader_assets.ref2va_unet_name"},
            )
        if reference_video_media_version_id is not None:
            raise DomainRuleError(
                "H3_REF2VA_UNAVAILABLE",
                "参考视频输入链未验证：MiniMaxH3ReferenceToVideo.ref_videos 期望逐帧图像输入，本机 manifest 将视频参考标记为 static_candidate_only；参考视频提交路径待集成评估",
                {
                    "reference_video_media_version_id": reference_video_media_version_id,
                    "route": "native_v2v",
                    "status": "static_candidate_only",
                },
            )
        if first_frame_media_version_id:
            if (
                canonical_relative_path(first_frame_media_version_id, code="H3_FIRST_FRAME_INVALID")
                != first_frame_media_version_id
            ):
                raise DomainRuleError("H3_FIRST_FRAME_INVALID", "H3 首帧必须是隔离 input root 内的相对文件名")
        else:
            raise DomainRuleError("H3_REF2VA_REFERENCE_REQUIRED", "Ref2V 至少需要一个参考输入（首帧参考图）")
        if not 4.0 <= duration_seconds <= 15.0:
            raise DomainRuleError("H3_DURATION_INVALID", "H3 duration 必须在 4—15 秒之间")
        self._validate_common(prompt, filename_prefix)
        assets = self.loader_assets()
        width, height = self._resolution(aspect_ratio)
        length = self._frame_count(duration_seconds)
        if tier is not None:
            resolved = self.resolve_tier(tier, aspect_ratio)
            width, height = resolved["width"], resolved["height"]
            length = resolved["frames"]
        steps = int(sigma_points)
        denoise = 1.0
        if tier is not None:
            resolved = self.resolve_tier(tier, aspect_ratio)
            steps = int(resolved["steps"])
            denoise = float(resolved["denoise"])
        workflow = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": assets["ref2va_unet_name"], "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": assets["text_encoder_name"], "type": "minimax", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": assets["video_vae_name"]}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": assets["audio_vae_name"]}},
            "5": {"class_type": "LoadImage", "inputs": {"image": first_frame_media_version_id}},
            "6": {"class_type": "ImageScale", "inputs": {"image": ["5", 0], "upscale_method": "lanczos", "width": width, "height": height, "crop": "disabled"}},
            "7": {
                "class_type": "MiniMaxH3ReferenceToVideo",
                "inputs": {"clip": ["2", 0], "vae": ["3", 0], "audio_vae": ["4", 0], "prompt": prompt, "width": width, "height": height, "length": length, "ref_image_size": "match", "ref_image_0": ["6", 0]},
            },
            "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
            "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
            "10": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "scheduler": "simple", "steps": steps, "denoise": denoise}},
            "11": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["7", 0]}},
            "12": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["8", 0], "guider": ["11", 0], "sampler": ["9", 0], "sigmas": ["10", 0], "latent_image": ["7", 1]}},
            "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}},
            "14": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
            "15": {"class_type": "CreateVideo", "inputs": {"images": ["13", 0], "fps": 24.0, "audio": ["14", 0], "bit_depth": 8}},
            "16": {"class_type": "SaveVideo", "inputs": {"video": ["15", 0], "filename_prefix": filename_prefix, "format": "mp4", "codec": "h264"}},
        }
        self._apply_runtime_overrides(
            workflow,
            assets=assets,
            acceleration=acceleration,
            lora_strength=lora_strength,
            native_audio=native_audio,
            audio_vae_node="4",
            audio_decode_node="14",
            create_video_node="15",
            audio_required=True,
        )
        return workflow

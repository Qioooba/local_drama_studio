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

# Fallback loader names; manifest ``authoritative_current_state.loader_assets``
# overrides each key when present.
_H3_LOADER_ASSETS = {
    "fl2va_unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    "text_encoder_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "video_vae_name": "minimax_h3_video_vae_fp16.safetensors",
    "audio_vae_name": "minimax_h3_audio_vae_fp32.safetensors",
    "turbo_lora_name": "minimax_h3_turbo_v4_step600_ema.safetensors",
}

# Verified on this host by openclaw (docs/H3_TURBO_PIPELINE.md): width/height
# must be divisible by 32 and the model snaps frame counts to the 17k+5 grid.
_H3_RESOLUTIONS = {
    "16:9": (864, 480),
    "9:16": (480, 832),
    "auto": (480, 832),
}

_H3_MODEL_SUBDIRS = {
    "fl2va_unet_name": "diffusion_models",
    "text_encoder_name": "text_encoders",
    "video_vae_name": "vae",
    "audio_vae_name": "vae",
    "turbo_lora_name": "loras",
}


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
        missing: list[str] = []
        for key, subdir in _H3_MODEL_SUBDIRS.items():
            candidate = comfy_root / "models" / subdir / assets[key]
            if not candidate.is_file():
                missing.append(str(candidate))
        return {
            "status": "PASS" if not missing else "BLOCKED",
            "release_root": str(comfy_root / "models") if comfy_root else "",
            "selected_components": self.candidate_assets(),
            "missing_model_files": missing,
            "node_family": "comfy_extras.MiniMaxH3ImageToVideo",
            "manifest_sha256": manifest.get("manifest_sha256"),
        }

    @staticmethod
    def _frame_count(duration_seconds: float) -> int:
        """Snap frames@24fps up to the model's 17k+5 grid (107 = ~4.46s, 124 = ~5.17s)."""
        target = round(duration_seconds * 24)
        remainder = target % 17
        if remainder != 5:
            target += (5 - remainder) % 17
        return target

    @staticmethod
    def _resolution(aspect_ratio: str) -> tuple[int, int]:
        ratio = str(aspect_ratio).strip().lower()
        if ratio not in _H3_RESOLUTIONS:
            raise DomainRuleError("H3_ASPECT_RATIO_UNSUPPORTED", "H3 分辨率仅支持 16:9 / 9:16 / auto", {"aspect_ratio": aspect_ratio})
        return _H3_RESOLUTIONS[ratio]

    def _validate_common(self, prompt: str, filename_prefix: str) -> None:
        if not prompt.strip():
            raise DomainRuleError("H3_PROMPT_REQUIRED", "H3 prompt 不能为空")
        if not filename_prefix or Path(filename_prefix).is_absolute() or ".." in Path(filename_prefix).parts:
            raise DomainRuleError("H3_OUTPUT_PREFIX_INVALID", "H3 输出 prefix 必须是相对路径")

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
    ) -> dict[str, Any]:
        """Compile the native MiniMax H3 T2V graph (core comfy_extras nodes)."""
        if not 4.0 <= duration_seconds <= 15.0:
            raise DomainRuleError("H3_DURATION_INVALID", "H3 duration 必须在 4—15 秒之间")
        self._validate_common(prompt, filename_prefix)
        assets = self.loader_assets()
        width, height = self._resolution(aspect_ratio)
        length = self._frame_count(duration_seconds)
        steps = int(sigma_points)
        return {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": assets["fl2va_unet_name"], "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": assets["text_encoder_name"], "type": "minimax", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": assets["video_vae_name"]}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": assets["audio_vae_name"]}},
            "5": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
            "6": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
            "7": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}},
            "8": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": prompt, "width": width, "height": height, "length": length}},
            "9": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["8", 0]}},
            "10": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["5", 0], "guider": ["9", 0], "sampler": ["6", 0], "sigmas": ["7", 0], "latent_image": ["8", 1]}},
            "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["3", 0]}},
            "12": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["10", 0], "vae": ["4", 0]}},
            "13": {"class_type": "CreateVideo", "inputs": {"images": ["11", 0], "fps": 24.0, "audio": ["12", 0], "bit_depth": 8}},
            "14": {"class_type": "SaveVideo", "inputs": {"video": ["13", 0], "filename_prefix": filename_prefix, "format": "mp4", "codec": "h264"}},
        }

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
    ) -> dict[str, Any]:
        """Compile the native MiniMax H3 first-frame FL2VA graph.

        ``first_frame`` is a Comfy input-root relative filename, never a host
        path. ComfyGenerationService materializes the verified MediaVersion.
        """
        if not first_frame or Path(first_frame).is_absolute() or ".." in Path(first_frame).parts:
            raise DomainRuleError("H3_FIRST_FRAME_INVALID", "H3 首帧必须是隔离 input root 内的相对文件名")
        if not 4.0 <= duration_seconds <= 15.0:
            raise DomainRuleError("H3_DURATION_INVALID", "H3 duration 必须在 4—15 秒之间")
        self._validate_common(prompt, filename_prefix)
        assets = self.loader_assets()
        width, height = self._resolution(aspect_ratio)
        length = self._frame_count(duration_seconds)
        steps = int(sigma_points)
        return {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": assets["fl2va_unet_name"], "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": assets["text_encoder_name"], "type": "minimax", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": assets["video_vae_name"]}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": assets["audio_vae_name"]}},
            "5": {"class_type": "LoadImage", "inputs": {"image": first_frame}},
            "6": {"class_type": "ImageScale", "inputs": {"image": ["5", 0], "upscale_method": "lanczos", "width": width, "height": height, "crop": "disabled"}},
            "7": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": prompt, "width": width, "height": height, "length": length, "first_frame": ["6", 0]}},
            "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
            "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
            "10": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}},
            "11": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["7", 0]}},
            "12": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["8", 0], "guider": ["11", 0], "sampler": ["9", 0], "sigmas": ["10", 0], "latent_image": ["7", 1]}},
            "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}},
            "14": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
            "15": {"class_type": "CreateVideo", "inputs": {"images": ["13", 0], "fps": 24.0, "audio": ["14", 0], "bit_depth": 8}},
            "16": {"class_type": "SaveVideo", "inputs": {"video": ["15", 0], "filename_prefix": filename_prefix, "format": "mp4", "codec": "h264"}},
        }

"""Manifest-backed H3 candidate workflow compiler; no automatic Profile activation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError


class H3WorkflowFactory:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _manifest(self) -> dict[str, Any]:
        try:
            manifest = json.loads(self.settings.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DomainRuleError("MANIFEST_INVALID", "H3 workflow 需要有效的本机 model_manifest.json") from error
        if not manifest.get("read_only_inventory"):
            raise DomainRuleError("MANIFEST_NOT_READ_ONLY", "模型 manifest 必须是只读 inventory")
        return manifest

    def candidate_assets(self) -> dict[str, str]:
        partitions = self._manifest().get("models", {}).get("partitions", {}).get("FL2VA", {})

        def component_selector(name: str) -> str:
            component = partitions.get(name)
            if not isinstance(component, dict) or not component.get("path"):
                raise KeyError(name)
            path = Path(str(component["path"]))
            if not path.is_absolute() or not path.parent.name:
                raise ValueError(name)
            # The installed RH node treats a flat weight filename as a request
            # for the legacy ``transformer``/``text_encoder`` sidecars.  The
            # manifest points at the actual INT8 component directories, so pass
            # those explicit directory selectors and keep the real sidecars.
            return path.parent.name

        try:
            return {
                "model_root": "MiniMax-H3",
                "text_encoder_path": component_selector("text_encoder"),
                "transformer_path": component_selector("transformer"),
                "video_vae_path": component_selector("video_vae"),
                "audio_vae_path": component_selector("audio_vae"),
            }
        except (KeyError, TypeError, ValueError) as error:
            raise DomainRuleError("H3_MANIFEST_ASSETS_MISSING", "manifest 缺少 FL2VA 候选资产") from error

    def runtime_layout(self) -> dict[str, Any]:
        """Check the exact sidecar layout required by the installed Comfy node.

        The read-only manifest remains authoritative for model files; this check only
        reports whether the local node can resolve their required release metadata.
        """
        manifest = self._manifest()
        partition = manifest.get("models", {}).get("partitions", {}).get("FL2VA", {})
        try:
            assets = self.candidate_assets()
            component_paths = {name: Path(str(partition[name]["path"])).parent for name in ("transformer", "text_encoder", "video_vae", "audio_vae")}
        except (DomainRuleError, KeyError, TypeError, ValueError) as error:
            return {"status": "BLOCKED", "missing": ["manifest.FL2VA.component_paths"], "error": str(error)}
        release_root = Path(str(partition.get("root") or component_paths["transformer"].parent))
        expected = [component_paths[name] / "config.json" for name in ("transformer", "text_encoder", "video_vae", "audio_vae")]
        missing = [str(path) for path in expected if not path.is_file()]
        model_files = [str(Path(str(component.get("path", "")))) for component in partition.values() if isinstance(component, dict) and component.get("path")]
        missing_files = [path for path in model_files if not Path(path).is_file()]
        return {
            "status": "PASS" if not missing and not missing_files else "BLOCKED",
            "release_root": str(release_root),
            "selected_components": assets,
            "required_sidecars": [str(path) for path in expected],
            "missing_sidecars": missing,
            "missing_model_files": missing_files,
            "manifest_sha256": manifest.get("manifest_sha256"),
        }

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
        if not prompt.strip():
            raise DomainRuleError("H3_PROMPT_REQUIRED", "H3 prompt 不能为空")
        if not 4.0 <= duration_seconds <= 15.0:
            raise DomainRuleError("H3_DURATION_INVALID", "H3 duration 必须在 4—15 秒之间")
        if not filename_prefix or Path(filename_prefix).is_absolute() or ".." in Path(filename_prefix).parts:
            raise DomainRuleError("H3_OUTPUT_PREFIX_INVALID", "H3 输出 prefix 必须是相对路径")
        assets = self.candidate_assets()
        return {
            "1": {
                "class_type": "RHMiniMaxH3DirectTextEncoderLoader",
                "inputs": {"model_root": assets["model_root"], "dtype": "auto", "text_encoder_path": assets["text_encoder_path"]},
            },
            "2": {
                "class_type": "RHMiniMaxH3DirectModelLoader",
                "inputs": {"model_root": assets["model_root"], "dtype": "auto", "transformer_path": assets["transformer_path"]},
            },
            "3": {
                "class_type": "RHMiniMaxH3DirectVAELoader",
                "inputs": {"model_root": assets["model_root"], "video_vae_path": assets["video_vae_path"], "audio_vae_path": assets["audio_vae_path"]},
            },
            "4": {"class_type": "RHMiniMaxH3T2VATarget", "inputs": {"aspect_ratio": aspect_ratio, "duration_seconds": duration_seconds}},
            "5": {"class_type": "RHMiniMaxH3T2VATextEncode", "inputs": {"h3_text_encoder": ["1", 0], "prompt": prompt}},
            "6": {"class_type": "RHMiniMaxH3EmptyAVLatent", "inputs": {"target": ["4", 0]}},
            "7": {
                "class_type": "RHMiniMaxH3DualSigmaSampler",
                "inputs": {
                    "h3_model": ["2", 0],
                    "conditioning": ["5", 0],
                    "av_latent": ["6", 0],
                    "seed": seed,
                    "sigma_points": sigma_points,
                    "video_shift": 12.0,
                    "audio_shift": 3.0,
                    "accel": acceleration,
                    "denoise_video": True,
                },
            },
            "8": {"class_type": "RHMiniMaxH3DecodeAV", "inputs": {"h3_vae_bundle": ["3", 0], "sampled_av_latent": ["7", 0]}},
            "9": {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "fps": 24.0, "bit_depth": 8}},
            "10": {"class_type": "SaveVideo", "inputs": {"video": ["9", 0], "filename_prefix": filename_prefix, "format": "mp4", "codec": "h264"}},
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
        """Build the installed node's first-frame FL2VA graph.

        ``first_frame`` is a Comfy input-root relative filename, never a host
        path. ComfyGenerationService materializes the verified MediaVersion.
        """
        if not prompt.strip():
            raise DomainRuleError("H3_PROMPT_REQUIRED", "H3 prompt 不能为空")
        if not first_frame or Path(first_frame).is_absolute() or ".." in Path(first_frame).parts:
            raise DomainRuleError("H3_FIRST_FRAME_INVALID", "H3 首帧必须是隔离 input root 内的相对文件名")
        if not 4.0 <= duration_seconds <= 15.0:
            raise DomainRuleError("H3_DURATION_INVALID", "H3 duration 必须在 4—15 秒之间")
        if not filename_prefix or Path(filename_prefix).is_absolute() or ".." in Path(filename_prefix).parts:
            raise DomainRuleError("H3_OUTPUT_PREFIX_INVALID", "H3 输出 prefix 必须是相对路径")
        assets = self.candidate_assets()
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": first_frame}},
            "2": {"class_type": "RHMiniMaxH3FL2VAFirstFrameCondition", "inputs": {"first_frame": ["1", 0]}},
            "3": {"class_type": "RHMiniMaxH3FL2VATextEncoderLoader", "inputs": {"model_root": assets["model_root"], "dtype": "auto", "text_encoder_path": assets["text_encoder_path"]}},
            "4": {"class_type": "RHMiniMaxH3FL2VAModelLoader", "inputs": {"model_root": assets["model_root"], "dtype": "auto", "transformer_path": assets["transformer_path"]}},
            "5": {"class_type": "RHMiniMaxH3FL2VAVAELoader", "inputs": {"model_root": assets["model_root"], "video_vae_path": assets["video_vae_path"], "audio_vae_path": assets["audio_vae_path"]}},
            "6": {"class_type": "RHMiniMaxH3FL2VATarget", "inputs": {"keyframes": ["2", 0], "aspect_ratio": aspect_ratio, "duration_seconds": duration_seconds}},
            "7": {"class_type": "RHMiniMaxH3FL2VAEncode", "inputs": {"h3_text_encoder": ["3", 0], "h3_vae_bundle": ["5", 0], "target": ["6", 0], "keyframes": ["2", 0], "prompt": prompt}},
            "8": {"class_type": "RHMiniMaxH3EmptyAVLatent", "inputs": {"target": ["6", 0]}},
            "9": {"class_type": "RHMiniMaxH3DualSigmaSampler", "inputs": {"h3_model": ["4", 0], "conditioning": ["7", 0], "av_latent": ["8", 0], "seed": seed, "sigma_points": sigma_points, "video_shift": 12.0, "audio_shift": 3.0, "accel": acceleration, "denoise_video": True}},
            "10": {"class_type": "RHMiniMaxH3DecodeAV", "inputs": {"h3_vae_bundle": ["5", 0], "sampled_av_latent": ["9", 0]}},
            "11": {"class_type": "CreateVideo", "inputs": {"images": ["10", 0], "fps": 24.0, "bit_depth": 8}},
            "12": {"class_type": "SaveVideo", "inputs": {"video": ["11", 0], "filename_prefix": filename_prefix, "format": "mp4", "codec": "h264"}},
        }
